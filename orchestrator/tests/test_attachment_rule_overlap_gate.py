# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-728 — the consent gate on rule creation, and the advisory route beside it.

The gate reuses LUM-729's shape exactly: a 422 carrying a machine-readable
`code`, an `acknowledge_*` boolean on the request model, and warn-then-allow
rather than refuse. Both systems are the user's; the failure being fixed is that
the consequence was invisible, not that the choice was wrong.

The load-bearing test here is `test_a_LOW_confidence_finding_NEVER_gates`. A
`low` match is *frequent* — the ordinary configuration that produces it is two
mailboxes on one provider — so gating on it would train users to dismiss the
dialogue, and the dismissal transfers to the `high` gate that mattered.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes.api_v1 import email_attachment_ingest as mod

import config
from services import paperless_overlap as po

SOURCE = "11111111-1111-1111-1111-111111111111"
#: A second mailbox the caller also owns. LUM-743: an acknowledgement given
#: for this one must not let a rule through on SOURCE.
OTHER_OWNED = "44444444-4444-4444-4444-444444444444"


class _Store:
    def __init__(self, owner="u1"):
        self.owner = owner

    def fetch_one(self, sql, params=None):
        if "FROM sources" in sql:
            if params[0] != SOURCE or params[1] != self.owner:
                return None
            return {"id": SOURCE, "extraction_method": "imap_full_ingest"}
        return None

    def fetch_all(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        return None


def _client(user_id="u1"):
    from auth import UserContext

    ctx = UserContext(user_id=user_id, role="member", allows_shared=True)
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_user] = lambda: ctx
    app.dependency_overrides[mod.require_same_origin] = lambda: None
    return TestClient(app)


@pytest.fixture
def store(monkeypatch):
    st = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)
    return st


def _overlap(monkeypatch, result):
    monkeypatch.setattr(mod.overlap_svc, "overlap_for_source", lambda *_a: result)


def _created(monkeypatch):
    made: list = []
    monkeypatch.setattr(
        mod.rules_svc,
        "create_rule",
        lambda *a: made.append(a) or {"id": "r1"},
    )
    return made


_BODY = {"source_id": SOURCE, "match_type": "sender", "match_value": "billing@x.com"}
_HIGH = po.OverlapResult(
    status="ok", overlap=po.Overlap(confidence="high", folder="INBOX", action=5)
)
_LOW = po.OverlapResult(status="ok", overlap=po.Overlap(confidence="low", folder="INBOX", action=5))


# --- the gate ----------------------------------------------------------------


def test_a_HIGH_overlap_refuses_with_the_machine_readable_code(store, monkeypatch):
    _overlap(monkeypatch, _HIGH)
    made = _created(monkeypatch)

    r = _client().post("/api/v1/email/attachment-rules", json=_BODY)

    assert r.status_code == 422
    assert r.json()["detail"]["code"] == po.OVERLAP_CODE
    assert made == [], "the rule must not be written before the question is answered"


def test_the_refusal_carries_consequence_alternative_and_recovery(store, monkeypatch):
    """A bare code is an error. The gate exists because the consequence was
    invisible, so the body has to say what happens, what else they could do, and
    what is recoverable."""
    _overlap(monkeypatch, _HIGH)
    _created(monkeypatch)

    detail = _client().post("/api/v1/email/attachment-rules", json=_BODY).json()["detail"]

    assert detail["message"] == po.MESSAGE
    assert detail["alternative"] == po.ALTERNATIVE
    assert detail["recovery"] == po.RECOVERY


def test_a_DELETE_rule_changes_the_message_only(store, monkeypatch):
    _overlap(
        monkeypatch,
        po.OverlapResult(status="ok", overlap=po.Overlap(confidence="high", action=1)),
    )
    _created(monkeypatch)

    r = _client().post("/api/v1/email/attachment-rules", json=_BODY)

    assert r.status_code == 422
    assert r.json()["detail"]["message"] == po.MESSAGE_DELETE


def test_acknowledging_lets_it_through(store, monkeypatch):
    """Warn-then-allow, not refuse. Both systems are the user's."""
    _overlap(monkeypatch, _HIGH)
    made = _created(monkeypatch)

    r = _client().post(
        "/api/v1/email/attachment-rules",
        # LUM-743: the acknowledgement names the mailbox it answers for.
        json={
            **_BODY,
            "acknowledge_paperless_overlap": True,
            "acknowledged_source_id": SOURCE,
        },
    )

    assert r.status_code == 201
    assert len(made) == 1


def test_an_acknowledged_request_does_not_even_probe(store, monkeypatch):
    """The probe costs a credential decrypt and two HTTP calls. Once the user has
    answered, asking paperless again buys nothing."""
    _created(monkeypatch)
    monkeypatch.setattr(
        mod.overlap_svc,
        "overlap_for_source",
        lambda *_a: pytest.fail("must not probe once acknowledged"),
    )

    r = _client().post(
        "/api/v1/email/attachment-rules",
        json={
            **_BODY,
            "acknowledge_paperless_overlap": True,
            "acknowledged_source_id": SOURCE,
        },
    )
    assert r.status_code == 201


def test_a_LOW_confidence_finding_NEVER_gates(store, monkeypatch):
    """**ADR D3.** `low` is frequent — two mailboxes on one provider is ordinary —
    so gating on it trains dismissal, and the dismissal transfers to the `high`
    gate that mattered."""
    _overlap(monkeypatch, _LOW)
    made = _created(monkeypatch)

    r = _client().post("/api/v1/email/attachment-rules", json=_BODY)

    assert r.status_code == 201
    assert len(made) == 1


@pytest.mark.parametrize("status", ["auth", "unavailable"])
def test_a_failed_probe_lets_the_rule_through(store, monkeypatch, status):
    """Fail open. A broken overlap check must never stop a user creating a rule
    on their own mailbox."""
    _overlap(monkeypatch, po.OverlapResult(status=status))
    made = _created(monkeypatch)

    r = _client().post("/api/v1/email/attachment-rules", json=_BODY)

    assert r.status_code == 201
    assert len(made) == 1


def test_another_users_source_is_rejected_before_any_probe(store, monkeypatch):
    monkeypatch.setattr(
        mod.overlap_svc,
        "overlap_for_source",
        lambda *_a: pytest.fail("ownership must be checked first"),
    )
    r = _client(user_id="intruder").post("/api/v1/email/attachment-rules", json=_BODY)
    assert r.status_code == 404


def test_the_overlap_422_is_distinguishable_from_an_invalid_rule_422(store, monkeypatch):
    """Both are 422 on the same route. The web branches on `code`; the existing
    invalid-rule path uses `error`. They must not collide."""
    _overlap(monkeypatch, po.OverlapResult(status="ok"))

    def _bad(*_a):
        raise ValueError("no such category")

    monkeypatch.setattr(mod.rules_svc, "create_rule", _bad)

    detail = _client().post("/api/v1/email/attachment-rules", json=_BODY).json()["detail"]

    assert detail.get("error") == "invalid_rule"
    assert "code" not in detail


# --- the advisory route ------------------------------------------------------


def test_GET_overlap_reports_low_with_the_advisory_copy(store, monkeypatch):
    """The GET exists because a `low` finding never gates, so the 422 can never
    carry it. Without this route the advisory has no delivery path at all."""
    _overlap(monkeypatch, _LOW)

    body = (
        _client()
        .get("/api/v1/email/attachment-ingest/paperless-overlap", params={"source_id": SOURCE})
        .json()
    )

    assert body == {
        "overlap": True,
        "confidence": "low",
        "status": "ok",
        "message": po.ADVISORY,
    }


def test_GET_overlap_never_leaks_the_folder(store, monkeypatch):
    """`folder` is internal diagnostic state — a folder name can be personal."""
    _overlap(
        monkeypatch,
        po.OverlapResult(
            status="ok", overlap=po.Overlap(confidence="high", folder="INBOX/Tax-2019", action=5)
        ),
    )

    r = _client().get(
        "/api/v1/email/attachment-ingest/paperless-overlap", params={"source_id": SOURCE}
    )

    assert "folder" not in r.json()
    assert "Tax-2019" not in r.text


def test_GET_overlap_reports_the_tri_state_when_there_is_nothing_to_report(store, monkeypatch):
    _overlap(monkeypatch, po.OverlapResult(status="unavailable"))

    body = (
        _client()
        .get("/api/v1/email/attachment-ingest/paperless-overlap", params={"source_id": SOURCE})
        .json()
    )

    assert body["overlap"] is False
    assert body["status"] == "unavailable"
    assert body["message"] is None


def test_GET_overlap_rejects_another_users_source(store, monkeypatch):
    _overlap(monkeypatch, _HIGH)
    r = _client(user_id="intruder").get(
        "/api/v1/email/attachment-ingest/paperless-overlap", params={"source_id": SOURCE}
    )
    assert r.status_code == 404


def test_GET_overlap_writes_nothing(store, monkeypatch):
    _overlap(monkeypatch, _HIGH)
    writes: list = []
    store.execute = lambda sql, params=None: writes.append(sql)

    _client().get("/api/v1/email/attachment-ingest/paperless-overlap", params={"source_id": SOURCE})

    assert writes == []


# --- LUM-743: the acknowledgement is scoped to the mailbox it names ----------


def test_consent_for_ANOTHER_mailbox_does_not_let_this_rule_through(store, monkeypatch):
    """**The reason LUM-743 exists.**

    Before this, `acknowledge_paperless_overlap` was a bare boolean: it could not
    say which mailbox the user had answered about. That was harmless while the
    review list showed one mailbox at a time, because "acknowledged" and
    "acknowledged for this mailbox" were the same statement. LUM-742 made the
    list span mailboxes, and the only thing then binding a consent to a mailbox
    was a client-side `Set` — one regression away from sending `true` for a rule
    on a mailbox the user had never been asked about, skipping the probe and
    filing everything twice.

    The server checks the claim now instead of trusting it.
    """
    _overlap(monkeypatch, _HIGH)
    made = _created(monkeypatch)

    r = _client().post(
        "/api/v1/email/attachment-rules",
        json={
            **_BODY,  # the rule is for SOURCE
            "acknowledge_paperless_overlap": True,
            "acknowledged_source_id": OTHER_OWNED,  # but the consent was for another
        },
    )

    assert r.status_code == 422
    assert r.json()["detail"]["code"] == po.OVERLAP_CODE
    assert made == [], "a consent about a different mailbox is not a consent about this one"


def test_a_bare_boolean_without_a_mailbox_fails_closed(store, monkeypatch):
    """An acknowledgement that names nothing is not an acknowledgement.

    Fail **closed** deliberately: the safe direction is asking again. A client
    that has not been updated re-prompts, which is visible and recoverable —
    whereas trusting the bare flag is the exact hole being closed.
    """
    _overlap(monkeypatch, _HIGH)
    made = _created(monkeypatch)

    r = _client().post(
        "/api/v1/email/attachment-rules",
        json={**_BODY, "acknowledge_paperless_overlap": True},
    )

    assert r.status_code == 422
    assert made == []


def test_a_mismatched_acknowledgement_re_asks_rather_than_erroring(store, monkeypatch):
    """The user sees the question again, not a failure.

    A mismatch is only reachable from a client defect, so it is logged — but the
    user-facing outcome must stay the ordinary consent question. Answering it
    correctly then works, which a hard error would not allow.
    """
    _overlap(monkeypatch, _HIGH)
    made = _created(monkeypatch)
    client = _client()

    refused = client.post(
        "/api/v1/email/attachment-rules",
        json={
            **_BODY,
            "acknowledge_paperless_overlap": True,
            "acknowledged_source_id": OTHER_OWNED,
        },
    )
    assert refused.status_code == 422

    accepted = client.post(
        "/api/v1/email/attachment-rules",
        json={
            **_BODY,
            "acknowledge_paperless_overlap": True,
            "acknowledged_source_id": SOURCE,
        },
    )

    assert accepted.status_code == 201
    assert len(made) == 1


def test_naming_a_mailbox_without_setting_the_flag_is_not_consent(store, monkeypatch):
    """Both halves are required. The id alone is a routing detail, not an answer."""
    _overlap(monkeypatch, _HIGH)
    made = _created(monkeypatch)

    r = _client().post(
        "/api/v1/email/attachment-rules",
        json={**_BODY, "acknowledged_source_id": SOURCE},
    )

    assert r.status_code == 422
    assert made == []
