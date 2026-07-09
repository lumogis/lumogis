# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-582 Rung 1 — conversation sharing: editable summary + household read path.

Exercises the real ``project_session`` override (+ preserve-on-omit) and the
``visible_filter()`` read path (collapse, member visibility, isolation) against
the in-memory sessions store. Cross-user is tested at the service layer because
the TestClient runs AUTH-disabled (single caller).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone

import pytest
from auth import UserContext
from tests.sessions_memory_store import SessionsMemoryMetadataStore

import config
from services import conversations as conv
from services import projection as proj

ALICE = "alice-uid"
BOB = "bob-uid"


@pytest.fixture
def store(monkeypatch):
    s = SessionsMemoryMetadataStore()
    config._instances["metadata_store"] = s
    return s


def _user(uid, allows_shared=True):
    return UserContext(user_id=uid, is_authenticated=True, role="user", allows_shared=allows_shared)


def _source(store, uid=ALICE, summary="AI summary"):
    sid = str(uuid.uuid4())
    store.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": summary,
        "topics": ["t"],
        "entities": [],
        "entity_ids": [],
        "user_id": uid,
        "scope": "personal",
        "published_from": None,
        "updated_at": datetime.now(timezone.utc),
    }
    return sid


def _src_dict(store, sid):
    r = store.sessions[sid]
    return {
        "session_id": r["session_id"],
        "summary": r["summary"],
        "topics": r["topics"],
        "entities": r["entities"],
        "entity_ids": r["entity_ids"],
    }


def _projection_row(store, sid):
    pk = proj.projection_pk("sessions", sid, "shared")
    return store.sessions.get(str(pk))


# --- editable summary override -------------------------------------------


def test_publish_with_edited_summary_projects_edited_not_source(store):
    sid = _source(store, summary="raw AI summary")
    proj.project_session(
        _src_dict(store, sid),
        target_scope="shared",
        actor=_user(ALICE),
        shared_summary="Human edit",
    )
    projection = _projection_row(store, sid)
    assert projection is not None
    assert projection["summary"] == "Human edit"  # projected text is the edit
    assert store.sessions[sid]["summary"] == "raw AI summary"  # source NEVER mutated


def test_first_share_without_override_uses_source_summary(store):
    sid = _source(store, summary="AI summary")
    proj.project_session(_src_dict(store, sid), target_scope="shared", actor=_user(ALICE))
    assert _projection_row(store, sid)["summary"] == "AI summary"


def test_republish_without_override_preserves_edit(store):
    sid = _source(store, summary="AI summary")
    proj.project_session(
        _src_dict(store, sid), target_scope="shared", actor=_user(ALICE), shared_summary="Edited"
    )
    # A bare re-publish (no override) must NOT revert to the raw AI summary.
    proj.project_session(_src_dict(store, sid), target_scope="shared", actor=_user(ALICE))
    assert _projection_row(store, sid)["summary"] == "Edited"


# --- read path: collapse, visibility, isolation --------------------------


def _share(store, sid, owner=ALICE, summary="Shared summary"):
    proj.project_session(
        _src_dict(store, sid), target_scope="shared", actor=_user(owner), shared_summary=summary
    )


def test_owner_sees_single_row_after_self_share(store):
    sid = _source(store, uid=ALICE)
    _share(store, sid)
    rows = conv.list_conversations(_user(ALICE))
    matching = [r for r in rows if r.conversation_id == sid]
    assert len(matching) == 1  # projection collapsed — one row, not two
    assert matching[0].share_status == "shared"
    assert matching[0].is_owner is True
    # And the projection id itself does NOT appear as a second row for the owner.
    proj_id = str(proj.projection_pk("sessions", sid, "shared"))
    assert all(r.conversation_id != proj_id for r in rows)


def test_member_sees_shared_conversation_not_owned(store):
    sid = _source(store, uid=ALICE)
    _share(store, sid)
    rows = conv.list_conversations(_user(BOB))
    proj_id = str(proj.projection_pk("sessions", sid, "shared"))
    shared = [r for r in rows if r.conversation_id == proj_id]
    assert len(shared) == 1
    assert shared[0].share_status == "shared"
    assert shared[0].is_owner is False


def test_member_never_sees_another_members_personal(store):
    _source(store, uid=ALICE)  # alice's personal, never shared
    rows = conv.list_conversations(_user(BOB))
    assert rows == []  # bob sees nothing — no shared items, no leak of alice's personal


def test_allows_shared_false_member_excluded_from_shared(store):
    """LUM-577 — a member with allows_shared=false sees no household-shared rows."""
    sid = _source(store, uid=ALICE)
    _share(store, sid)
    opted_out = _user(BOB, allows_shared=False)
    assert conv.list_conversations(opted_out) == []  # shared arm excluded
    # And the detail path is likewise gated (opaque not-found).
    proj_id = str(proj.projection_pk("sessions", sid, "shared"))
    with pytest.raises(conv.ConversationNotFoundError):
        conv.get_conversation(opted_out, proj_id)


def test_get_shared_conversation_shows_shared_summary_for_member(store):
    sid = _source(store, uid=ALICE)
    _share(store, sid, summary="Household text")
    proj_id = str(proj.projection_pk("sessions", sid, "shared"))
    detail = conv.get_conversation(_user(BOB), proj_id)
    assert detail.is_owner is False
    assert detail.share_status == "shared"
    assert detail.shared_summary == "Household text"
    assert detail.messages == []  # Rung 1 shares the summary, not the transcript


def test_get_own_shared_source_exposes_shared_summary(store):
    sid = _source(store, uid=ALICE, summary="private AI")
    _share(store, sid, summary="Household text")
    detail = conv.get_conversation(_user(ALICE), sid)
    assert detail.is_owner is True
    assert detail.share_status == "shared"
    # The owner's row carries the household-facing text (for the composer prefill),
    # resolved from the projection — distinct from their private source summary.
    assert detail.shared_summary == "Household text"
    assert detail.summary == "private AI"


def test_web_only_conversation_cannot_share(store):
    cid = str(uuid.uuid4())
    store.web_conversations[f"{cid}:{ALICE}"] = {
        "conversation_id": uuid.UUID(cid),
        "user_id": ALICE,
        "title": "draft chat",
        "model": "m",
        "message_count": 3,
        "scope": "personal",
        "updated_at": datetime.now(timezone.utc),
    }
    detail = conv.get_conversation(_user(ALICE), cid)
    assert detail.can_share is False  # no backing sessions row → not shareable yet
    assert detail.share_status == "personal"
