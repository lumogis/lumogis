# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-728 — does paperless watch *this* mailbox?

The failure this module exists to avoid is **absence**: a wrong answer here does
not raise, it silently declines to warn. So most of these tests assert that a
thing *happened* (a log line, a `high` tier reached, an empty list returned)
rather than that nothing broke.

Three of them pin decisions that a reasonable implementer would otherwise get
wrong in the direction that kills the feature:

* **port must not participate in the match** — paperless's `imap_port` is
  nullable and blank is the common case, so requiring equality would make `high`
  unreachable for most real installs;
* **the auth header must match the shipped poller's** — a wrong one is a 401,
  which maps to `auth`, which fails open, so the gate would never fire and every
  fake-based test would still pass;
* **`status != "ok"` must empty both lists** — a partial read lets the matcher
  return a confident "no overlap" from half the data.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

import config
from services import external_consumer_guard as ecg
from services import paperless_overlap as po


class _Store:
    """LUM-744: `fetch_all` was added when the overlap check became a batch.

    The fake previously modelled `fetch_one` only, because the check read one
    mailbox at a time. `overlap_for_sources` reads them in one query, so the fake
    has to answer that query — this is a fixture keeping up with a private seam,
    not a behaviour change. `queries` records call counts so a test can assert the
    batch issues ONE sources read rather than one per mailbox.
    """

    def __init__(self, url="imap://imap.gmail.com:993/personal"):
        self.url = url
        self.queries: list[str] = []

    def fetch_one(self, sql, params=None):
        self.queries.append("fetch_one")
        return {"url": self.url} if self.url else None

    def fetch_all(self, sql, params=None):
        self.queries.append("fetch_all")
        if not self.url:
            return []
        # params = (user_id, [source_ids]); every requested id maps to the same
        # url, which is what the single-mailbox tests assume.
        ids = (params or (None, []))[1] or []
        return [{"id": sid, "url": self.url} for sid in ids]


class _Conn:
    base_url = "https://paperless.lan"
    token = "t"  # noqa: S105 - fixture value, not a credential


def _install(
    monkeypatch,
    *,
    accounts=None,
    rules=None,
    status="ok",
    username="me@gmail.com",
    url="imap://imap.gmail.com:993/personal",
    has_paperless=True,
):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store(url))
    monkeypatch.setattr(ecg, "has_external_consumer", lambda _u: has_paperless)
    monkeypatch.setattr(po.paperless_credentials, "load_connection", lambda _u, **_k: _Conn())
    monkeypatch.setattr(po, "_mailbox_username", lambda *_a, **_k: username)

    import adapters.paperless_source as ps

    monkeypatch.setattr(
        ps.PaperlessMailConfigReader,
        "fetch_mail_config",
        lambda self: (accounts or [], rules or [], status),
    )


_ACC = [{"id": 7, "imap_server": "imap.gmail.com", "username": "me@gmail.com"}]
_RULE = [{"id": 1, "account": 7, "folder": "INBOX", "action": 5}]


# --- the hard constraint -----------------------------------------------------


def test_no_paperless_is_a_complete_noop(monkeypatch):
    """No HTTP client constructed, no decrypt attempted. If this regresses, every
    self-hoster without paperless pays for a feature they do not use."""
    calls = []
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store())
    monkeypatch.setattr(ecg, "has_external_consumer", lambda _u: False)
    monkeypatch.setattr(
        po.paperless_credentials,
        "load_connection",
        lambda _u, **_k: calls.append("loaded") or _Conn(),
    )
    monkeypatch.setattr(po, "_mailbox_username", lambda *a, **k: calls.append("decrypt"))

    out = po.overlap_for_source("u1", "s1")

    assert out == po.OverlapResult(status="ok")
    assert calls == [], "the precondition must short-circuit before any work"


def test_disabled_by_env_is_a_noop(monkeypatch):
    monkeypatch.setenv("PAPERLESS_OVERLAP_CHECK_ENABLED", "false")
    monkeypatch.setattr(
        ecg, "has_external_consumer", lambda _u: pytest.fail("must not be consulted")
    )
    assert po.overlap_for_source("u1", "s1").status == "ok"


# --- the matching predicate --------------------------------------------------


def test_username_and_host_match_yields_HIGH(monkeypatch):
    _install(monkeypatch, accounts=_ACC, rules=_RULE)
    out = po.overlap_for_source("u1", "s1")
    assert out.overlap is not None and out.overlap.confidence == "high"


def test_host_only_match_yields_LOW(monkeypatch):
    """Two mailboxes on one provider — the ordinary household case, and the one
    where a confident match would stop filing on the wrong mailbox."""
    _install(monkeypatch, accounts=_ACC, rules=_RULE, username="someone.else@gmail.com")
    out = po.overlap_for_source("u1", "s1")
    assert out.overlap is not None and out.overlap.confidence == "low"


def test_a_NULL_paperless_imap_port_still_reaches_HIGH(monkeypatch):
    """**Port must not participate.** `MailAccount.imap_port` is nullable and
    blank is the common case; requiring equality makes `high` unreachable for
    most real installs — a permanently dead gate no fake would catch."""
    acc = [
        {"id": 7, "imap_server": "imap.gmail.com", "username": "me@gmail.com", "imap_port": None}
    ]
    _install(monkeypatch, accounts=acc, rules=_RULE)
    assert po.overlap_for_source("u1", "s1").overlap.confidence == "high"


def test_host_compare_is_casefolded_and_trailing_dot_tolerant(monkeypatch):
    acc = [{"id": 7, "imap_server": "IMAP.Gmail.COM.", "username": "me@gmail.com"}]
    _install(monkeypatch, accounts=acc, rules=_RULE)
    assert po.overlap_for_source("u1", "s1").overlap is not None


def test_a_different_host_is_not_an_overlap(monkeypatch):
    acc = [{"id": 7, "imap_server": "imap.fastmail.com", "username": "me@gmail.com"}]
    _install(monkeypatch, accounts=acc, rules=_RULE)
    out = po.overlap_for_source("u1", "s1")
    assert out.status == "ok" and out.overlap is None


def test_usernames_are_compared_WITHOUT_local_part_normalisation(monkeypatch):
    """Gmail's dot-folding is not generalisable, and guessing it wrong produces a
    false `high` — the only outcome that gates wrongly."""
    acc = [{"id": 7, "imap_server": "imap.gmail.com", "username": "m.e@gmail.com"}]
    _install(monkeypatch, accounts=acc, rules=_RULE)
    assert po.overlap_for_source("u1", "s1").overlap.confidence == "low"


@pytest.mark.parametrize("folder", ["INBOX", "inbox", " InBox ", None, ""])
def test_INBOX_in_any_case_or_absent_is_compatible(monkeypatch, folder):
    _install(
        monkeypatch, accounts=_ACC, rules=[{"id": 1, "account": 7, "folder": folder, "action": 5}]
    )
    assert po.overlap_for_source("u1", "s1").overlap is not None


@pytest.mark.parametrize("folder", ["INBOX/Receipts", "INBOX.Receipts", "Archive"])
def test_a_deeper_folder_is_NOT_an_overlap_either_delimiter(monkeypatch, folder):
    """Lumogis reads INBOX only, so the `.` vs `/` delimiter question never
    arises — nothing but bare INBOX matches, so no delimiter is ever compared."""
    _install(
        monkeypatch, accounts=_ACC, rules=[{"id": 1, "account": 7, "folder": folder, "action": 5}]
    )
    out = po.overlap_for_source("u1", "s1")
    assert out.status == "ok" and out.overlap is None


def test_a_DELETE_rule_wins_selection_and_changes_only_the_COPY(monkeypatch):
    rules = [
        {"id": 9, "account": 7, "folder": "INBOX", "action": 5},
        {"id": 3, "account": 7, "folder": "INBOX", "action": 1},
    ]
    _install(monkeypatch, accounts=_ACC, rules=rules)
    ov = po.overlap_for_source("u1", "s1").overlap
    assert ov.action == 1
    assert po.refusal_detail(ov)["message"] == po.MESSAGE_DELETE
    # ...and control flow is identical: still `high`, still a finding.
    assert ov.confidence == "high"


def test_selection_is_deterministic_on_id_when_no_delete(monkeypatch):
    rules = [
        {"id": 9, "account": 7, "folder": "INBOX", "action": 5},
        {"id": 3, "account": 7, "folder": "INBOX", "action": 4},
    ]
    _install(monkeypatch, accounts=_ACC, rules=rules)
    assert po.overlap_for_source("u1", "s1").overlap.action == 4


# --- the contract that the first draft got wrong -----------------------------


def test_status_survives_when_there_is_no_overlap(monkeypatch):
    """`ok`+none and `unavailable`+none must be distinguishable — the bare
    `Overlap | None` of the first draft destroyed the status the GET requires."""
    _install(monkeypatch, accounts=[], rules=[], status="ok")
    assert po.overlap_for_source("u1", "s1") == po.OverlapResult(status="ok")

    _install(monkeypatch, status="unavailable")
    assert po.overlap_for_source("u1", "s1") == po.OverlapResult(status="unavailable")


def test_auth_status_is_distinct_from_unavailable(monkeypatch):
    _install(monkeypatch, status="auth")
    assert po.overlap_for_source("u1", "s1").status == "auth"


# --- failing open, loudly ----------------------------------------------------


def test_any_unexpected_exception_fails_OPEN(monkeypatch, caplog):
    """Specifically the whitespace-token case: `load_connection` raises a bare
    `ValueError`, which a literal reading of the error table would have let 500
    the POST — fail-closed, the one posture this design forbids."""
    _install(monkeypatch, accounts=_ACC, rules=_RULE)

    def _boom(_u, **_k):
        raise ValueError("paperless payload field 'token' has leading/trailing whitespace")

    monkeypatch.setattr(po.paperless_credentials, "load_connection", _boom)

    with caplog.at_level(logging.WARNING, logger=po.__name__):
        out = po.overlap_for_source("u1", "s1")

    assert out == po.OverlapResult(status="unavailable")
    assert "paperless_overlap.failed" in caplog.text


def test_a_decrypt_failure_degrades_to_LOW_rather_than_raising(monkeypatch):
    _install(monkeypatch, accounts=_ACC, rules=_RULE, username=None)
    assert po.overlap_for_source("u1", "s1").overlap.confidence == "low"


# --- the feature's only sign of life -----------------------------------------


@pytest.mark.parametrize("status", ["ok", "auth", "unavailable"])
def test_every_probe_emits_exactly_one_outcome_line(monkeypatch, caplog, status):
    """Without this the gate can ship green and be dead on arrival — probe always
    timing out, or always permission-filtered — and nothing would show it."""
    _install(monkeypatch, accounts=_ACC, rules=_RULE, status=status)
    with caplog.at_level(logging.INFO, logger=po.__name__):
        po.overlap_for_source("u1", "s1")
    lines = [r for r in caplog.records if "paperless_overlap_probe" in r.getMessage()]
    assert len(lines) == 1
    assert f"outcome={status}" in lines[0].getMessage()


def test_the_outcome_line_leaks_no_identifiers(monkeypatch, caplog):
    _install(monkeypatch, accounts=_ACC, rules=_RULE)
    with caplog.at_level(logging.INFO, logger=po.__name__):
        po.overlap_for_source("u1", "s1")
    text = caplog.text
    for secret in ("me@gmail.com", "imap.gmail.com", "u1", "INBOX"):
        assert secret not in text, f"{secret!r} must not reach the probe log line"


# --- the adapter's bounded read ----------------------------------------------


class _Resp:
    """A streamed response. The reader streams rather than calling `.json()` so
    it can enforce a wall-clock deadline: `httpx.Timeout` bounds each phase, and
    a host that drips the body resets the read timeout forever."""

    def __init__(self, code=200, payload=None, text_body=None, chunks=None):
        self.status_code = code
        self._body = (
            text_body.encode()
            if text_body is not None
            else json.dumps(payload).encode()
            if payload is not None
            else b""
        )
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self):
        if self._chunks is not None:
            yield from self._chunks
            return
        yield self._body


def _reader(monkeypatch, responses):
    import adapters.paperless_source as ps

    seen = []

    class _Client:
        def __init__(self, *a, **k):
            self.kw = k

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, params=None, headers=None):
            seen.append({"method": method, "url": url, "params": params, "headers": headers})
            return responses[len(seen) - 1]

    monkeypatch.setattr(ps.httpx, "Client", _Client)
    return ps.PaperlessMailConfigReader(_Conn()), seen


def test_the_reader_sends_the_SAME_auth_header_as_the_shipped_poller(monkeypatch):
    """Asserted against `PaperlessPoller._headers()` itself, never a copy — a
    wrong header is a 401 → `auth` → fail open → the gate never fires, forever."""
    import adapters.paperless_source as ps

    reader, seen = _reader(
        monkeypatch,
        [_Resp(payload={"results": []}), _Resp(payload={"results": []})],
    )
    reader.fetch_mail_config()

    expected = ps.PaperlessPoller(_Conn())._headers()
    assert seen[0]["headers"] == expected


def test_page_size_100_is_requested(monkeypatch):
    reader, seen = _reader(
        monkeypatch, [_Resp(payload={"results": []}), _Resp(payload={"results": []})]
    )
    reader.fetch_mail_config()
    assert all(c["params"] == {"page_size": 100} for c in seen)


def test_a_truncated_list_is_UNAVAILABLE_not_a_partial_ok(monkeypatch, caplog):
    """`ok` promises the lists are complete. A matching rule on page 2 would
    otherwise yield a confident "no overlap" — the silent false negative this
    whole feature exists to prevent. We do not know, so we say so."""
    reader, _ = _reader(
        monkeypatch,
        [
            _Resp(payload={"results": [{"id": 1}], "next": "http://x?page=2", "count": 200}),
            _Resp(payload={"results": []}),
        ],
    )
    import adapters.paperless_source as ps

    with caplog.at_level(logging.WARNING, logger=ps.__name__):
        accounts, rules, status = reader.fetch_mail_config()
    assert "paperless_overlap_truncated" in caplog.text
    assert (accounts, rules, status) == ([], [], "unavailable")


@pytest.mark.parametrize(
    "resp,expect",
    [
        (_Resp(401), "auth"),
        (_Resp(403), "auth"),
        (_Resp(404), "unavailable"),
        (_Resp(500), "unavailable"),
        (_Resp(200, text_body="<html>"), "unavailable"),
        (_Resp(200, payload={"detail": "nope"}), "unavailable"),
    ],
)
def test_each_HTTP_outcome_maps_to_the_right_status(monkeypatch, resp, expect):
    reader, _ = _reader(monkeypatch, [resp, resp])
    _accounts, _rules, status = reader.fetch_mail_config()
    assert status == expect


def test_a_non_ok_status_returns_EMPTY_lists_never_partial(monkeypatch):
    """Accounts fetched, rules failed — matching against the half that arrived
    would return a confident "no overlap" from incomplete data."""
    reader, _ = _reader(
        monkeypatch,
        [_Resp(payload={"results": [{"id": 7}]}), _Resp(500)],
    )
    accounts, rules, status = reader.fetch_mail_config()
    assert (accounts, rules, status) == ([], [], "unavailable")


def test_an_unknown_envelope_key_does_not_break_parsing(monkeypatch):
    """Paperless's `StandardPagination` adds `all`; a strict envelope check would
    turn a version bump into a permanent fail-open."""
    reader, _ = _reader(
        monkeypatch,
        [
            _Resp(
                payload={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "all": [1],
                    "results": [{"id": 7}],
                }
            ),
            _Resp(payload={"results": []}),
        ],
    )
    accounts, _rules, status = reader.fetch_mail_config()
    assert status == "ok" and accounts == [{"id": 7}]


def test_a_connection_error_is_unavailable_not_an_exception(monkeypatch):
    import adapters.paperless_source as ps

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, *a, **k):
            raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(ps.httpx, "Client", _Client)
    assert ps.PaperlessMailConfigReader(_Conn()).fetch_mail_config() == ([], [], "unavailable")


# --- the two structural guarantees -------------------------------------------


def test_the_reader_never_uses_the_retrying_request_path():
    """`_request_json` retries 5x at 60s — ~5.5 minutes against an unreachable
    host. Reusing it here would hang the rule-creation form."""
    import ast
    import inspect
    import textwrap

    import adapters.paperless_source as ps

    # Parsed, not grepped: the class docstring *names* `_request_json` on purpose,
    # to record why it is not used. A text search would fail on the explanation
    # rather than on the behaviour, and the fix would be to delete the
    # explanation — exactly backwards.
    tree = ast.parse(textwrap.dedent(inspect.getsource(ps.PaperlessMailConfigReader)))
    referenced = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    }

    assert "_request_json" not in referenced
    assert "_MAX_ATTEMPTS" not in referenced


def test_the_service_does_not_import_httpx():
    """Services in this codebase do not perform backend I/O. The bounded-client
    requirement is an adapter concern; inside matching logic it would be easy to
    'simplify' away."""
    from pathlib import Path

    src = Path(po.__file__).read_text()
    assert "import httpx" not in src


def test_the_reader_accepts_only_a_connection_never_a_raw_url():
    """SSRF: the base URL can only arrive via `load_connection`, which runs
    `_validate_outbound_and_token`."""
    import inspect

    import adapters.paperless_source as ps

    params = inspect.signature(ps.PaperlessMailConfigReader.__init__).parameters
    assert set(params) == {"self", "connection", "deadline_s"}


# --- review-round fixes ------------------------------------------------------
#
# Each of these pins a defect found by review AFTER the first implementation
# passed 56 tests of its own. They share a shape: the wrong behaviour was
# *silent*, so the original tests could not have caught it — the feature simply
# would have declined to warn, forever, with nothing to show.


def test_a_DISABLED_paperless_rule_is_not_an_overlap(monkeypatch):
    """A rule the user switched off files nothing, so claiming it will file
    everything twice is simply false — and it is a `high` gate the user cannot
    make sense of, on a mailbox where nothing is wrong."""
    _install(
        monkeypatch,
        accounts=_ACC,
        rules=[{"id": 1, "account": 7, "folder": "INBOX", "action": 5, "enabled": False}],
    )
    out = po.overlap_for_source("u1", "s1")
    assert out.status == "ok" and out.overlap is None


def test_a_rule_without_an_enabled_key_is_treated_as_enabled(monkeypatch):
    """Older paperless versions may not serialise it, and defaulting to
    'disabled' would turn the whole gate off against those installs."""
    _install(monkeypatch, accounts=_ACC, rules=_RULE)
    assert po.overlap_for_source("u1", "s1").overlap is not None


def test_the_legacy_instance_row_mailbox_can_still_reach_HIGH(monkeypatch):
    """A mailbox configured before LUM-170 lives at `instance=""`, not per source.

    Migration 054 added `instance` with `DEFAULT ''`, so pre-multi-instance rows
    are still there — and `_validate_instance(..., write=True)` now refuses to
    create one, so no write path can ever recreate it. Without an explicit read
    the username is never found in that deployment, confidence pins to `low`
    forever, and since `low` never gates, the gating half of the feature is
    silently dead there.

    **`AUTH_ENABLED` is set explicitly** (LUM-778): the fallback is now guarded
    to the single-mailbox deployment, and this test previously passed only
    because `auth_enabled()` happens to default false — it named a deployment it
    was not actually in.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    seen: list = []

    def _payload(_user, _connector, *, instance=""):
        seen.append(instance)
        return {"username": "me@gmail.com"} if instance == "" else None

    monkeypatch.setattr(po.ccs, "get_payload", _payload)
    assert po._mailbox_username("u1", "s1") == "me@gmail.com"
    assert seen == ["s1", ""], "the per-source instance must still be tried first"


def test_the_legacy_row_is_not_borrowed_across_mailboxes_when_auth_is_on(monkeypatch):
    """The half of the old fallback that was wrong, and is now gone (LUM-778).

    `_mailbox_username`'s fallback was unguarded: with AUTH on and several
    mailboxes, a source whose own credential row was missing would resolve the
    username from whatever sat at `instance=""` — one mailbox's address read out
    of another mailbox's credential, then compared against paperless' configured
    accounts to decide `high` vs `low`. A wrong `high` is the direction that
    costs something: `high` gates.

    `instance=""` is not reachable by any write for a multi-instance connector,
    so with AUTH on there is no legitimate row to find.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    seen: list = []

    def _payload(_user, _connector, *, instance=""):
        seen.append(instance)
        return {"username": "someone-elses@gmail.com"} if instance == "" else None

    monkeypatch.setattr(po.ccs, "get_payload", _payload)
    assert po._mailbox_username("u1", "s1") is None
    assert seen == ["s1"], "the legacy row must not be consulted when AUTH is on"


def test_an_unreadable_credential_skips_the_mailbox_rather_than_raising(monkeypatch):
    """The error shape is the caller's decision, and this caller's is "skip".

    `mailbox_account_address` raises so each caller must say what an unknown
    address means. Converging onto it (LUM-778) must not make the overlap path
    start throwing where it used to skip — `overlap_for_sources` runs this once
    per mailbox, so one unreadable credential would take a whole household's
    check down with it.
    """

    def _boom(*_a, **_k):
        raise RuntimeError("decrypt failed")

    monkeypatch.setattr(po.email_credentials, "mailbox_account_address", _boom)
    assert po._mailbox_username("u1", "s1") is None


def test_a_whitespace_padded_username_degrades_to_low_rather_than_matching(monkeypatch):
    """A deliberate delta from the code this replaced (LUM-778), pinned.

    The old local read returned any non-empty string, so `" me@gmail.com "` was
    compared against paperless' accounts and could reach `high`. The shared
    accessor's `_require_str` refuses padding — the same check `host` already
    gets, with the same "no strip" reasoning — so it now resolves to `None`.

    That state is reachable: `validate_imap_instance_payload_against_source`
    checks host and port on a credential PUT, not username.

    Accepted rather than worked around, because the direction is safe. `high`
    gates and `low` does not, so this can only warn less firmly, never refuse
    wrongly — and the advisory still renders, so the user is still told. The
    alternative was a local `.strip()` here, which is the second implementation
    this ticket exists to delete.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(po.ccs, "get_payload", lambda *_a, **_k: {"username": "  me@gmail.com  "})
    assert po._mailbox_username("u1", "s1") is None


def test_there_is_one_implementation_of_reading_a_mailbox_username(monkeypatch):
    """The point of LUM-778, pinned so it cannot quietly regrow.

    Two copies of this helper agreed on a normal mailbox, which is exactly why
    the divergence went unnoticed — LUM-813 exists because `_message_id_for`
    shipped twice and the incomplete copy won. If someone reinstates a local
    payload read here, the accessor stops being consulted and this fails.
    """
    calls: list = []
    monkeypatch.setattr(
        po.email_credentials,
        "mailbox_account_address",
        lambda uid, *, instance: calls.append((uid, instance)) or "me@gmail.com",
    )
    assert po._mailbox_username("u1", "s1") == "me@gmail.com"
    assert calls == [("u1", "s1")], "the username must come from the shared accessor"


def test_a_malformed_source_url_does_not_raise(monkeypatch):
    """`urlsplit(...).port` RAISES on a non-numeric port, and it sat outside the
    guard — one bad row turned every probe for that mailbox into a traceback."""
    assert po._mailbox_host_port("imap://host:not-a-port/label") is None
    assert po._mailbox_host_port("imap://host:99999/label") is None


def test_the_kill_switch_does_not_look_like_a_broken_probe(monkeypatch, caplog):
    """`.env.example` tells operators to watch `outcome=`. Reporting
    `unavailable` while the check is deliberately off poisons the one signal the
    feature has."""
    monkeypatch.setenv("PAPERLESS_OVERLAP_CHECK_ENABLED", "false")
    with caplog.at_level(logging.INFO, logger=po.__name__):
        po.overlap_for_source("u1", "s1")
    assert "outcome=disabled" in caplog.text


def test_a_dripping_body_is_abandoned_at_the_deadline(monkeypatch):
    """`httpx.Timeout` bounds each PHASE, not the call: a host that drips one
    byte at a time resets the read timeout forever and never trips it. On a
    request path that is the exact hang this class exists to prevent."""
    import adapters.paperless_source as ps

    ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0])

    def _drip():
        yield b'{"resu'
        yield b'lts": []}'

    monkeypatch.setattr(ps.time, "monotonic", lambda: next(ticks, 99.0))
    reader, _ = _reader(monkeypatch, [_Resp(chunks=_drip()), _Resp(payload={"results": []})])

    assert reader.fetch_mail_config() == ([], [], "unavailable")


# ---------------------------------------------------------------------------
# LUM-741 — the DNS resolve is inside the deadline, and its expiry is not
# mistakable for "this host has no A/AAAA records".
# ---------------------------------------------------------------------------


def test_a_blackholed_resolver_is_abandoned_at_the_deadline(monkeypatch):
    """A resolver that never answers must not outlive PAPERLESS_OVERLAP_TIMEOUT_S.

    Before LUM-741 the SSRF check's `getaddrinfo` ran BEFORE the reader's clock
    started, so a blackholed resolver hung the rule-creation click for as long as
    the OS resolver took — well past the advertised ceiling.
    """
    import time as _time

    monkeypatch.setenv("PAPERLESS_OVERLAP_TIMEOUT_S", "0.5")

    def _never_answers(_hostname):
        _time.sleep(30)
        return ["203.0.113.1"]

    resolver = po._bounded_resolver(_time.monotonic() + 0.5)
    started = _time.monotonic()
    with pytest.raises(po.OutboundResolveDeadline):
        # Patch the module-level default the bounded wrapper delegates to.
        import services.outbound_http_url as ohu

        monkeypatch.setattr(ohu, "_resolved_ips", _never_answers)
        resolver("blackhole.example.com")
    elapsed = _time.monotonic() - started
    assert elapsed < 5, f"resolver was not abandoned at the deadline ({elapsed:.1f}s)"


def test_a_resolver_deadline_is_not_swallowed_as_a_dns_failure():
    """THE TRAP. `OutboundResolveDeadline` must not be an `OSError`.

    `validate_outbound_connector_base_url` treats `OSError` from the resolver as
    "no A/AAAA records" and returns SUCCESSFULLY, applying scheme + host-shape
    checks only. `TimeoutError` -- the obvious choice for a timeout -- IS a
    subclass of `OSError` (and `socket.timeout` is an alias of it since 3.10), so
    raising that would be swallowed there and the probe would go on to issue HTTP
    against a host whose IP policy was never evaluated.

    That is a slow resolver becoming an SSRF-validation bypass, which LUM-741
    explicitly forbids. This test is the guard on that property.
    """
    assert not issubclass(po.OutboundResolveDeadline, OSError), (
        "OutboundResolveDeadline must NOT derive from OSError -- "
        "validate_outbound_connector_base_url catches OSError and fails OPEN"
    )
    # And the premise the above depends on, asserted rather than assumed:
    assert issubclass(TimeoutError, OSError)


def test_the_resolver_deadline_propagates_through_the_real_validator(monkeypatch):
    """End-to-end on the property: the validator must NOT swallow the deadline.

    Asserted against the real `validate_outbound_connector_base_url` rather than a
    fake, because the whole risk lives in that function's `except OSError: return`.
    """
    import services.outbound_http_url as ohu

    def _expired(_hostname):
        raise po.OutboundResolveDeadline("expired")

    with pytest.raises(po.OutboundResolveDeadline):
        ohu.validate_outbound_connector_base_url(
            "https://paperless.example.com", resolve_host=_expired
        )

    # Control: a genuine resolution failure still fails OPEN, unchanged.
    def _no_records(_hostname):
        raise OSError("NXDOMAIN")

    ohu.validate_outbound_connector_base_url(
        "https://paperless.example.com", resolve_host=_no_records
    )


def test_the_dns_budget_is_shared_with_the_http_budget(monkeypatch):
    """Time spent resolving is DEDUCTED from the reader's budget, not added to it.

    The ticket's requirement is that the bound be "shared with -- not additional
    to -- PAPERLESS_OVERLAP_TIMEOUT_S", so the documented ceiling becomes true
    rather than approximately true. Pinned by capturing the `deadline_s` the
    reader is actually constructed with after a slow-but-successful resolve.
    """
    import adapters.paperless_source as ps

    monkeypatch.setenv("PAPERLESS_OVERLAP_TIMEOUT_S", "5")
    seen = {}

    class _CapturingReader:
        def __init__(self, conn, deadline_s=None):
            seen["deadline_s"] = deadline_s

        def fetch_mail_config(self):
            return ([], [], "ok")

    def _slow_load(_u, **_k):
        # Burn a measurable slice of the shared budget inside the credential load.
        import time as _t

        _t.sleep(0.2)
        return _Conn()

    _install(monkeypatch, accounts=_ACC, rules=_RULE)
    monkeypatch.setattr(po.paperless_credentials, "load_connection", _slow_load)
    monkeypatch.setattr(ps, "PaperlessMailConfigReader", _CapturingReader)

    po.overlap_for_source("alice", "src-1")

    assert "deadline_s" in seen, "the reader was never constructed"
    assert seen["deadline_s"] is not None, "reader got the default budget, not the remainder"
    assert seen["deadline_s"] < 5.0, "DNS time was not deducted -- the budget is additive"
    assert seen["deadline_s"] > 4.0, f"unexpectedly small remainder: {seen['deadline_s']}"


def test_a_normal_resolution_still_reaches_the_reader(monkeypatch):
    """The happy path is unchanged: a fast resolver still produces an overlap."""
    _install(monkeypatch, accounts=_ACC, rules=_RULE)
    result = po.overlap_for_source("alice", "src-1")
    assert result.overlap is not None
    assert result.overlap.confidence == "high"
