# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The ``email_unsubscribe`` action (LUM-697).

Two of these exist because the first draft of this plan got them wrong:

* ``test_unsubscribe_forces_ask_even_in_do_mode`` drives the **real** resolution
  path. The original spec declared an ``annotate`` hook returning
  ``{"bulk_unsubscribe": True}``, which ``executor._resolve_force_ask`` ignores
  entirely — it reads ``first_contact`` and nothing else — so the action would
  have auto-approved for any user with the ``smtp`` connector in DO mode. A test
  that called ``check_permission(force_ask=True)`` directly would have passed
  against that broken wiring and certified the hole.
* ``test_one_message_per_target_never_one_multi_recipient_send`` pins the
  send topology: a single multi-recipient message would put every other
  unsubscribe address in ``To:`` and leak the user's whole subscription list to
  each recipient.

LUM-710 moved execution off the request thread, so the topology and idempotence
tests now drive ``run_unsubscribe_batch`` — the loop both the worker and the
inline fallback share — while the tests below it pin what ``_email_unsubscribe``
itself now does: gate ownership, then enqueue.
"""

from __future__ import annotations

import pytest
from actions.executor import _resolve_force_ask
from actions.executor import is_hard_limited
from actions.handlers import smtp_send
from actions.registry import get_action
from pydantic import ValidationError

import config

_UID = "user-A"
_SID = "11111111-1111-1111-1111-111111111111"


class _Conn:
    host = "smtp.x.com"
    port = 587
    username = "me@x.com"
    password = "pw"
    auth_type = "app_password"
    oauth_access_token = None


class _Store:
    """Tracks the unsubscribed_at stamp so retry-idempotency is real, not mocked."""

    def __init__(self):
        self.stamped: set[str] = set()

    def fetch_one(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        if "select unsubscribed_at from email_unsubscribe_target" in ql:
            addr = (params or ("", "", ""))[2]
            return {"unsubscribed_at": "now"} if addr in self.stamped else {"unsubscribed_at": None}
        return None

    def fetch_all(self, q, params=None):
        return []

    def execute(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        if "update email_unsubscribe_target set unsubscribed_at" in ql:
            self.stamped.add((params or ("", "", ""))[2])


@pytest.fixture
def env(monkeypatch):
    """Real build_message + a capturing transport, so From/injection invariants
    are exercised rather than stubbed away."""
    store = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)

    from adapters.email_smtp_sender import SendResult
    from adapters.email_smtp_sender import SmtpSender

    sends: list = []

    def _fake_send(self, msg, **kw):
        sends.append(msg)
        return SendResult(message_id=msg["Message-ID"] or "<mid@x>", refused=())

    monkeypatch.setattr(SmtpSender, "send", _fake_send, raising=True)
    monkeypatch.setattr(smtp_send, "_resolve_smtp", lambda sid, uid: _Conn())
    monkeypatch.setattr(smtp_send, "_load_owned_source", lambda sid, uid: object())
    return {"sends": sends, "store": store}


def _payload(n=2):
    return {
        "source_id": _SID,
        "targets": [
            {"from_addr": f"news{i}@x.com", "mailto_addr": f"u{i}@x.com", "subject": "unsubscribe"}
            for i in range(n)
        ],
    }


def _run(n=2, user_id=_UID):
    """Drive the shared send loop the way the worker does."""
    return smtp_send.run_unsubscribe_batch(
        smtp_send.EmailUnsubscribeInput.model_validate(_payload(n)), user_id=user_id
    )


# --------------------------------------------------------------------------- #
# the privilege-hole regression — through the REAL resolution path
# --------------------------------------------------------------------------- #
def test_unsubscribe_forces_ask_even_in_do_mode():
    spec = get_action("email_unsubscribe")
    annotation = spec.annotate(_payload(), _UID)
    # The real resolver, not check_permission(force_ask=True).
    assert _resolve_force_ask(spec, _payload(), _UID, approved=False, annotation=annotation) is True


def test_annotate_unsubscribe_annotation_actually_force_asks():
    """Asserts the EFFECT, not the dict. Asserting the payload's contents would
    pass against the broken spec by construction."""
    spec = get_action("email_unsubscribe")
    ann = spec.annotate({}, _UID)
    assert _resolve_force_ask(spec, {}, _UID, approved=False, annotation=ann) is True


def test_unsubscribe_is_not_hard_limited():
    """Force-Ask, not hard-limited: a hard-limited action is refused even on the
    approved-proposal path, which would make an approved batch unexecutable."""
    assert is_hard_limited("email_unsubscribe") is False


def test_email_unsubscribe_registered_with_the_right_shape():
    spec = get_action("email_unsubscribe")
    assert spec is not None
    assert spec.connector == "smtp"
    assert spec.action_type == "email_unsubscribe"
    assert spec.is_write is True
    assert spec.is_reversible is False
    assert spec.annotate is not None, "without an annotate hook a write is NEVER force-asked"


# --------------------------------------------------------------------------- #
# send topology
# --------------------------------------------------------------------------- #
def test_one_message_per_target_never_one_multi_recipient_send(env):
    _run(3)
    sends = env["sends"]
    assert len(sends) == 3
    for i, msg in enumerate(sends):
        assert msg["To"] == f"u{i}@x.com"
        for j in range(3):
            if j != i:
                assert f"u{j}@x.com" not in msg.as_string(), "leaked another target's address"


def test_from_is_always_the_authenticated_account(env):
    _run(1)
    assert env["sends"][0]["From"] == "me@x.com"


def test_ownership_gate_runs_before_any_connect(monkeypatch):
    from actions.errors import PermanentActionError

    order: list[str] = []

    def _deny(sid, uid):
        order.append("owned")
        raise PermanentActionError("not_found", "not yours")

    monkeypatch.setattr(smtp_send, "_load_owned_source", _deny)
    monkeypatch.setattr(
        smtp_send,
        "_resolve_smtp",
        lambda sid, uid: order.append("connect") or _Conn(),
    )
    with pytest.raises(PermanentActionError):
        smtp_send._email_unsubscribe(_payload(1), user_id="someone-else")
    assert order == ["owned"], "credentials must not be resolved for a foreign mailbox"


# --------------------------------------------------------------------------- #
# batch progress + idempotent retry
# --------------------------------------------------------------------------- #
def test_each_target_is_stamped_as_its_own_send_succeeds(env):
    _run(3)
    assert env["store"].stamped == {"news0@x.com", "news1@x.com", "news2@x.com"}


def test_retry_skips_already_unsubscribed_targets(env):
    _run(3)
    env["sends"].clear()
    sent, skipped = _run(3)
    assert env["sends"] == [], "a completed batch must not re-send"
    assert sent == [] and len(skipped) == 3


def test_partially_completed_batch_sends_only_the_remainder(env):
    env["store"].stamped.add("news0@x.com")
    _run(3)
    assert [m["To"] for m in env["sends"]] == ["u1@x.com", "u2@x.com"]


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def test_batch_over_cap_rejected_at_validation():
    smtp_send.EmailUnsubscribeInput.model_validate(_payload(smtp_send.MAX_UNSUBSCRIBE_BATCH))
    with pytest.raises(ValidationError):
        smtp_send.EmailUnsubscribeInput.model_validate(
            _payload(smtp_send.MAX_UNSUBSCRIBE_BATCH + 1)
        )


def test_empty_batch_rejected():
    with pytest.raises(ValidationError):
        smtp_send.EmailUnsubscribeInput.model_validate({"source_id": _SID, "targets": []})


def test_malformed_recipient_rejected_at_validation():
    with pytest.raises(ValidationError):
        smtp_send.EmailUnsubscribeInput.model_validate(
            {
                "source_id": _SID,
                "targets": [{"from_addr": "a@x.com", "mailto_addr": "not-an-address"}],
            }
        )


def test_crlf_in_subject_rejected_at_validation():
    with pytest.raises(ValidationError):
        smtp_send.EmailUnsubscribeInput.model_validate(
            {
                "source_id": _SID,
                "targets": [
                    {
                        "from_addr": "a@x.com",
                        "mailto_addr": "u@x.com",
                        "subject": "x\r\nBcc: evil@x.com",
                    }
                ],
            }
        )


def test_input_model_forbids_a_client_supplied_from_field():
    """Anti-spoofing: there is no `from` on any send input in this family."""
    with pytest.raises(ValidationError):
        smtp_send.EmailUnsubscribeInput.model_validate({**_payload(1), "from": "someone@else.com"})
