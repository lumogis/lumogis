# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Memory-scope isolation against real stores (ADR 015, LUM-36).

ADR 015 puts one visibility rule in front of three stores::

    (scope = 'personal' AND user_id = $me) OR scope IN ('shared','system')

The headline test (``tests/test_household_sharing.py``) pins that contract
against an in-memory store. This module proves it **end to end against real
Postgres, Qdrant and FalkorDB**, for the *memory* surfaces — session
summaries and indexed notes in the ``conversations`` collection (what chat and
MCP ``memory.search`` recall), the MCP ``memories`` write surface (LUM-291),
and graph nodes — with a household of three members, one member who has opted
out of shared scope (LUM-577), and one admin.

What each test proves:

* personal isolation — a member's personal session never reaches another
  member's recall (Qdrant ANN) or recent-sessions read (Postgres), and the
  filter is what excludes it (an unfiltered search finds it);
* household visibility — a shared projection reaches every member who allows
  shared scope, and no further;
* publish / unpublish — the scope change lands in Qdrant payloads (the
  visibility filter starts and stops matching), not only in Postgres;
* shared notes — a published note is recalled with its text by every member who
  allows shared scope, and a note shared before LUM-954 (old payload keys) is
  still read (``test_note_projected_before_the_fix_still_recalls``);
* delete — purging a conversation removes the personal source and its shared
  projection from both stores;
* admin — on normal retrieval an admin follows the member rule; the admin
  governance path (``admin_unshare``) is retract-only, refuses a never-shared
  personal item with the opaque not-found, and writes an audit row;
* MCP memories — owner-only recall, no household union;
* FalkorDB — the Cypher fragment selects the same rows as the other two.

Embeddings come from the directory's deterministic hash embedder
(``_paperless_live_stores.py``): identical text gives cosine 1.0, unrelated
text gives roughly 0, so "would have matched without the filter" is decidable
and every negative assertion here has a positive control.

No live audio-memo case: no production writer creates personal ``audio_memos``
rows today, and ``tests/test_conversation_payload_contract.py`` drives
``project_audio_memo`` and the reader. Add one if an audio ingest path returns.

Skips without a backend on a laptop; fails under
``scripts/test-env.sh run --tier 2`` (``LUMOGIS_REQUIRE_INTEGRATION_DB=1``).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace

import pytest
from auth import UserContext
from models.memory import SessionSummary
from services.point_ids import note_conversation_point_id
from visibility import visible_cypher_fragment
from visibility import visible_qdrant_filter

import config
from services import admin_unshare as admin_svc
from services import memory
from services import memory_purge
from services import projection

CONV = "conversations"
MEM = "memories"
_DIM = 768  # matches _paperless_live_stores._VECTOR_SIZE


# ---------------------------------------------------------------------------
# Household fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def household(live_stores, monkeypatch):
    """Five real ``users`` rows over real Postgres + Qdrant; torn down after.

    alice, bob, carol — members who allow shared scope; dana — a member who
    opted out (``allows_shared=FALSE``); root — an admin.
    """
    ms, vs = live_stores
    for coll in (CONV, MEM):
        vs.create_collection(coll, _DIM)  # idempotent
    # The graph arm of purge is not under test here (FalkorDB is exercised
    # directly below); None is the supported "graph disabled" value.
    monkeypatch.setattr(config, "get_graph_store", lambda *a, **k: None)

    suffix = uuid.uuid4().hex[:8]
    users: dict[str, str] = {}
    for name, role, allows_shared in (
        ("alice", "user", True),
        ("bob", "user", True),
        ("carol", "user", True),
        ("dana", "user", False),
        ("root", "admin", True),
    ):
        uid = f"lum36-{name}-{suffix}"
        ms.execute(
            "INSERT INTO users (id, email, password_hash, role, disabled, allows_shared) "
            "VALUES (%s, %s, %s, %s, FALSE, %s)",
            (uid, f"{uid}@home.lan", "not-a-real-hash", role, allows_shared),
        )
        users[name] = uid

    h = SimpleNamespace(ms=ms, vs=vs, u=users, suffix=suffix)
    try:
        yield h
    finally:
        ids = list(users.values())
        for sql in (
            "DELETE FROM sessions WHERE user_id = ANY(%s)",
            "DELETE FROM notes WHERE user_id = ANY(%s)",
            "DELETE FROM memories WHERE user_id = ANY(%s)",
            "DELETE FROM purged_conversations WHERE user_id = ANY(%s)",
            "DELETE FROM audit_log WHERE user_id = ANY(%s)",
            "DELETE FROM users WHERE id = ANY(%s)",
        ):
            try:
                ms.execute(sql, (ids,))
            except Exception:
                pass
        for coll in (CONV, MEM):
            for uid in ids:
                try:
                    vs.delete_where(coll, {"must": [{"key": "user_id", "match": {"value": uid}}]})
                except Exception:
                    pass


def _member(uid: str, role: str = "user") -> UserContext:
    """An authenticated caller, as a JWT-backed route would build it."""
    return UserContext(user_id=uid, role=role, is_authenticated=True)


def _embed_text(summary: str, topics: list[str]) -> str:
    # store_session / project_session embed exactly this string.
    return f"{summary} Topics: {', '.join(topics)}"


def _store_personal_session(h, owner: str, label: str) -> tuple[str, str]:
    """Write a personal session through the production writer (Qdrant + Postgres).

    Returns ``(session_id, query)`` where ``query`` embeds to the stored vector.
    """
    sid = str(uuid.uuid4())
    summary = f"lum36 {label} {uuid.uuid4().hex}"
    topics = [f"t-{label}"]
    memory.store_session(
        SessionSummary(session_id=sid, summary=summary, topics=topics, entities=[]),
        user_id=owner,
    )
    return sid, _embed_text(summary, topics)


def _session_row(h, sid: str) -> dict:
    row = h.ms.fetch_one(
        "SELECT session_id, summary, topics, entities, entity_ids, user_id, scope "
        "FROM sessions WHERE session_id = %s::uuid",
        (sid,),
    )
    assert row is not None, f"personal session {sid} missing from Postgres"
    return row


def _recalled_ids(query: str, uid: str) -> set[str]:
    return {hit.session_id for hit in memory.retrieve_context(query, limit=10, user_id=uid)}


def _recent_ids(uid: str) -> set[str]:
    return {str(s.session_id) for s in memory.recent_sessions(limit=1000, user_id=uid)}


def _visible_count(h, user: UserContext, clause: dict, collection: str = CONV) -> int:
    """Points matching ``clause`` that ``user``'s visibility filter admits.

    AND-composed under a parent ``must`` exactly as visibility.py prescribes
    (never alongside the helper's ``should``, which would broaden it).
    """
    return h.vs.count_where(collection, {"must": [visible_qdrant_filter(user), clause]})


def _published_from(src_pk: str) -> dict:
    return {"key": "published_from", "match": {"value": src_pk}}


def _session_payload(sid: str) -> dict:
    return {"key": "session_id", "match": {"value": sid}}


# ---------------------------------------------------------------------------
# Personal isolation
# ---------------------------------------------------------------------------


def test_personal_session_never_reaches_another_member(household):
    h = household
    alice, bob = h.u["alice"], h.u["bob"]
    a_sid, a_query = _store_personal_session(h, alice, "alice-private")
    b_sid, b_query = _store_personal_session(h, bob, "bob-private")

    # Both stores hold Alice's row as personal, owned by Alice.
    assert _session_row(h, a_sid)["scope"] == "personal"
    assert h.vs.count_where(CONV, {"must": [_session_payload(a_sid)]}) == 1

    # Negative control: without a filter, Alice's point is the top hit for her
    # text — so its absence below is the filter's doing, not the embedder's.
    unfiltered = h.vs.search(
        collection=CONV, vector=config.get_embedder().embed(a_query), limit=5, threshold=0.4
    )
    assert any((r["payload"] or {}).get("session_id") == a_sid for r in unfiltered)

    # Recall (Qdrant ANN) — owners find their own, never each other's.
    assert a_sid in _recalled_ids(a_query, alice)
    assert b_sid in _recalled_ids(b_query, bob)
    assert a_sid not in _recalled_ids(a_query, bob)
    assert b_sid not in _recalled_ids(b_query, alice)

    # Recent sessions (Postgres visible_filter) — same rule in the other store.
    assert a_sid in _recent_ids(alice) and a_sid not in _recent_ids(bob)
    assert b_sid in _recent_ids(bob) and b_sid not in _recent_ids(alice)

    # The raw filter, for members who never wrote anything.
    for name in ("carol", "dana"):
        assert _visible_count(h, _member(h.u[name]), _session_payload(a_sid)) == 0


# ---------------------------------------------------------------------------
# Household (shared) visibility + publish / unpublish
# ---------------------------------------------------------------------------


def test_publish_reaches_every_member_and_unpublish_withdraws_it_from_qdrant(household):
    h = household
    alice = h.u["alice"]
    sid, query = _store_personal_session(h, alice, "meal-plan")
    src = _session_row(h, sid)
    proj_id = projection.projection_pk("sessions", sid, "shared")

    members = [h.u["bob"], h.u["carol"]]
    # Before publish: no other member's Qdrant filter matches anything for it.
    for uid in members:
        assert _visible_count(h, _member(uid), _published_from(sid)) == 0
        assert _visible_count(h, _member(uid), _session_payload(sid)) == 0

    projection.project_session(src, target_scope="shared", actor=_member(alice))

    # Qdrant: one shared point, attributed to the publisher, pointing back.
    shared_clause = {"must": [_published_from(sid), {"key": "scope", "match": {"value": "shared"}}]}
    assert h.vs.count_where(CONV, shared_clause) == 1
    # Every member who allows shared scope now matches it — in Qdrant ...
    for uid in [alice, *members]:
        assert _visible_count(h, _member(uid), _published_from(sid)) == 1, uid
        assert proj_id in _recalled_ids(query, uid), uid
        # ... and in Postgres.
        assert proj_id in _recent_ids(uid), uid
    # The personal source is still Alice's alone (projection, not mutation).
    for uid in members:
        assert sid not in _recalled_ids(query, uid)
        assert sid not in _recent_ids(uid)
    assert _session_row(h, sid)["scope"] == "personal"

    # A member who opted out of shared scope (LUM-577) sees neither copy.
    dana = h.u["dana"]
    assert _visible_count(h, _member(dana), _published_from(sid)) == 0
    assert not ({sid, proj_id} & _recalled_ids(query, dana))
    assert not ({sid, proj_id} & _recent_ids(dana))

    # Unpublish: the Qdrant point is gone, so no member's filter matches it.
    assert projection.unproject_session(sid) == 1
    assert h.vs.count_where(CONV, {"must": [_published_from(sid)]}) == 0
    for uid in members:
        assert _visible_count(h, _member(uid), _published_from(sid)) == 0
        assert proj_id not in _recalled_ids(query, uid)
        assert proj_id not in _recent_ids(uid)
    assert h.ms.fetch_one("SELECT 1 FROM sessions WHERE session_id = %s::uuid", (proj_id,)) is None
    # Alice's personal source survives in both stores.
    assert sid in _recalled_ids(query, alice)
    assert sid in _recent_ids(alice)


def test_note_publish_and_unpublish_move_the_qdrant_payload(household):
    """Notes share the one ``conversations`` payload shape (LUM-954), so recall reads them."""
    h = household
    alice, bob, carol, dana = (h.u[n] for n in ("alice", "bob", "carol", "dana"))
    text = f"lum36 note {uuid.uuid4().hex}"
    row = h.ms.fetch_one(
        "INSERT INTO notes (text, user_id, source, scope) "
        "VALUES (%s, %s, 'lumogis_web_capture', 'personal') RETURNING *",
        (text, alice),
    )
    note_id = str(row["note_id"])
    # Mirror the capture-index writer (services/captures.py).
    h.vs.upsert(
        collection=CONV,
        id=note_conversation_point_id(alice, note_id),
        vector=config.get_embedder().embed(text),
        payload=memory.conversation_payload(
            item_id=note_id, summary=text, user_id=alice, scope="personal", note_id=note_id
        ),
    )
    personal = _session_payload(note_id)
    assert _visible_count(h, _member(alice), personal) == 1
    assert _visible_count(h, _member(bob), personal) == 0

    projection.project_note(row, target_scope="shared", actor=_member(alice))
    proj_id = projection.projection_pk("notes", note_id, "shared")
    assert _visible_count(h, _member(bob), _published_from(note_id)) == 1
    assert _visible_count(h, _member(bob), personal) == 0  # source stays private

    # Recall returns the shared note with its text (LUM-954), not a blank hit.
    for uid in (bob, carol):
        hits = {hit.session_id: hit.summary for hit in memory.retrieve_context(text, 10, uid)}
        assert hits.get(proj_id) == text, f"{uid} did not recall the shared note: {hits}"
    assert not {note_id, proj_id} & _recalled_ids(text, dana)  # opted out of shared scope
    assert note_id not in _recalled_ids(text, bob)  # source stays private
    assert {note_id, proj_id} <= _recalled_ids(text, alice)  # her own copy and the shared one

    assert projection.unproject_note(note_id) == 1
    assert h.vs.count_where(CONV, {"must": [_published_from(note_id)]}) == 0
    assert _visible_count(h, _member(bob), _published_from(note_id)) == 0
    assert proj_id not in _recalled_ids(text, bob)
    assert _visible_count(h, _member(alice), personal) == 1


def test_note_projected_before_the_fix_still_recalls(household):
    """A note shared before LUM-954 carries ``note_id``/``text``; recall still reads it."""
    h = household
    alice, bob, dana = h.u["alice"], h.u["bob"], h.u["dana"]
    text = f"lum36 legacy note {uuid.uuid4().hex}"
    row = h.ms.fetch_one(
        "INSERT INTO notes (text, user_id, source, scope) "
        "VALUES (%s, %s, 'lumogis_web_capture', 'personal') RETURNING note_id",
        (text, alice),
    )
    note_id = str(row["note_id"])
    proj_id = projection.projection_pk("notes", note_id, "shared")
    h.ms.execute(
        "INSERT INTO notes (note_id, text, user_id, source, scope, published_from) "
        "VALUES (%s, %s, %s, 'lumogis_web_capture', 'shared', %s)",
        (proj_id, text, alice, note_id),
    )
    h.vs.upsert(
        collection=CONV,
        id=projection.projection_point_id(CONV, note_id, "shared"),
        vector=config.get_embedder().embed(text),
        payload={  # the shape project_note wrote before the fix
            "note_id": proj_id,
            "text": text,
            "user_id": alice,
            "scope": "shared",
            "published_from": note_id,
        },
    )

    hits = {hit.session_id: hit.summary for hit in memory.retrieve_context(text, 10, bob)}
    assert hits.get(proj_id) == text
    assert not {note_id, proj_id} & _recalled_ids(text, dana)
    assert projection.unproject_note(note_id) == 1  # point-id delete, payload keys irrelevant
    assert proj_id not in _recalled_ids(text, bob)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_purge_removes_personal_and_shared_copies_from_both_stores(household):
    h = household
    alice, bob = h.u["alice"], h.u["bob"]
    sid, query = _store_personal_session(h, alice, "to-forget")
    projection.project_session(_session_row(h, sid), target_scope="shared", actor=_member(alice))
    proj_id = projection.projection_pk("sessions", sid, "shared")
    assert proj_id in _recalled_ids(query, bob)

    # Only the owner can purge: Bob's attempt finds no target and touches nothing.
    noop = memory_purge.purge_session_memory(user_id=bob, session_id=sid)
    assert not noop.postgres_deleted
    assert h.vs.count_where(CONV, {"must": [_published_from(sid)]}) == 1

    result = memory_purge.purge_session_memory(user_id=alice, session_id=sid)
    assert result.postgres_deleted and result.qdrant_deleted, result.errors
    assert not result.partial

    assert (
        h.ms.fetch_one(
            "SELECT 1 FROM sessions WHERE session_id = %s::uuid OR published_from = %s::uuid",
            (sid, sid),
        )
        is None
    )
    assert h.vs.count_where(CONV, {"must": [_session_payload(sid)]}) == 0
    assert h.vs.count_where(CONV, {"must": [_published_from(sid)]}) == 0
    assert (
        h.vs.count_where(
            CONV,
            {"must": [{"key": "user_id", "match": {"value": alice}}]},
        )
        == 0
    )
    for uid in (alice, bob):
        assert not ({sid, proj_id} & _recalled_ids(query, uid))
        assert not ({sid, proj_id} & _recent_ids(uid))

    # The tombstone stops a late writer from resurrecting it.
    memory.store_session(
        SessionSummary(session_id=sid, summary="resurrected", topics=[], entities=[]),
        user_id=alice,
    )
    assert h.ms.fetch_one("SELECT 1 FROM sessions WHERE session_id = %s::uuid", (sid,)) is None
    assert h.vs.count_where(CONV, {"must": [_session_payload(sid)]}) == 0


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def test_admin_follows_member_rule_and_unshare_is_retract_only(household):
    h = household
    alice, root = h.u["alice"], h.u["root"]
    admin = _member(root, role="admin")
    sid, query = _store_personal_session(h, alice, "admin-probe")

    # Normal retrieval: the admin is a member like any other (ADR 015).
    assert _visible_count(h, admin, _session_payload(sid)) == 0
    assert sid not in _recalled_ids(query, root)
    assert sid not in _recent_ids(root)

    # Governance: a never-shared personal item is an opaque not-found — the
    # path cannot be used to probe for private items — and nothing is audited.
    with pytest.raises(admin_svc.SharedItemNotFound):
        admin_svc.admin_unshare(actor=admin, resource="sessions", pk=sid)
    assert h.ms.fetch_one("SELECT 1 FROM audit_log WHERE user_id = %s", (root,)) is None
    listed = {i["resource_id"] for i in admin_svc.admin_list_shared_items(limit=10_000)}
    assert sid not in listed

    projection.project_session(_session_row(h, sid), target_scope="shared", actor=_member(alice))
    assert sid in {i["resource_id"] for i in admin_svc.admin_list_shared_items(limit=10_000)}
    assert _visible_count(h, admin, _published_from(sid)) == 1

    out = admin_svc.admin_unshare(actor=admin, resource="sessions", pk=sid)
    assert out == {
        "resource_type": "sessions",
        "resource_id": sid,
        "source_owner_id": alice,
        "unshared": True,
    }
    # Retracted from both stores; Alice's personal source untouched.
    assert h.vs.count_where(CONV, {"must": [_published_from(sid)]}) == 0
    assert (
        h.ms.fetch_one(
            "SELECT 1 FROM sessions WHERE published_from = %s::uuid AND scope = 'shared'", (sid,)
        )
        is None
    )
    assert _session_row(h, sid)["scope"] == "personal"
    assert sid in _recalled_ids(query, alice)
    # Audited against the acting admin.
    audit = h.ms.fetch_one(
        "SELECT action_name, result_summary FROM audit_log WHERE user_id = %s", (root,)
    )
    assert audit is not None and audit["action_name"] == "admin_unshare"
    assert alice in audit["result_summary"]


# ---------------------------------------------------------------------------
# MCP memories (LUM-291) — owner-only, no household arm
# ---------------------------------------------------------------------------


def test_mcp_memory_recall_is_owner_only(household):
    from services import memories
    from services import recall

    h = household
    alice, bob, root = h.u["alice"], h.u["bob"], h.u["root"]
    marker = uuid.uuid4().hex
    content = f"lum36 alice keeps the spare key under {marker}"
    mid = memories.store_memory(user_id=alice, bank="coding", content=content)
    # Bob has a memory of his own so his temporal leg is non-empty.
    memories.store_memory(user_id=bob, bank="coding", content=f"lum36 bob {uuid.uuid4().hex}")

    assert h.vs.count_where(MEM, {"must": [{"key": "memory_id", "match": {"value": mid}}]}) == 1

    def ids(uid: str, bank: str) -> set[str]:
        return {m.id for m in recall.recall(user_id=uid, bank=bank, query=content, rerank=False)}

    assert mid in ids(alice, "coding")
    for uid in (bob, root):  # a member and the admin alike
        assert mid not in ids(uid, "coding")
        assert mid not in ids(uid, "*")  # cross-bank widens banks, never users
    assert memories.get_memory(mid, user_id=bob) is None

    # Hydration re-applies ``user_id`` in Postgres, so the fused result above
    # would stay owner-only even if a retrieval leg leaked. Pin the legs too,
    # so each layer is proven on its own rather than one masking the other.
    emb = config.get_embedder()
    assert mid in recall._leg_semantic(content, user_id=alice, bank="coding", embedder=emb, vs=h.vs)
    assert mid not in recall._leg_semantic(content, user_id=bob, bank="*", embedder=emb, vs=h.vs)
    now = datetime.now(timezone.utc)
    assert mid in recall._leg_bm25(marker, user_id=alice, bank="coding", as_of=now, ms=h.ms)
    assert mid not in recall._leg_bm25(marker, user_id=bob, bank="*", as_of=now, ms=h.ms)


# ---------------------------------------------------------------------------
# FalkorDB — the third store applies the same rule
# ---------------------------------------------------------------------------


@pytest.fixture
def falkordb_graph():
    from adapters.falkordb_store import FalkorDBStore
    from tests.integration._pgguard import require_postgres

    url = os.environ.get("FALKORDB_URL", "")
    name = f"lum36_scope_{uuid.uuid4().hex[:8]}"
    try:
        if not url:
            raise RuntimeError("FALKORDB_URL not set")
        gs = FalkorDBStore(url=url, graph_name=name)
        if not gs.ping():
            raise RuntimeError("FalkorDB ping failed")
    except Exception as exc:  # pragma: no cover - env-dependent
        require_postgres(exc, what="FalkorDB")
    try:
        yield gs
    finally:
        try:
            gs._graph().delete()
        except Exception:
            pass


def test_cypher_fragment_admits_the_same_rows_in_falkordb(household, falkordb_graph):
    h, gs = household, falkordb_graph
    alice, bob, dana, root = h.u["alice"], h.u["bob"], h.u["dana"], h.u["root"]
    # Personal nodes MERGE on (lumogis_id, user_id); the shared projection on
    # (lumogis_id, scope) — ADR 015's conditional MERGE key.
    for lid, uid, scope in (
        ("e-alice", alice, "personal"),
        ("e-bob", bob, "personal"),
        ("e-alice", alice, "shared"),
        ("e-sys", "system", "system"),
    ):
        gs.query(
            "CREATE (:Entity {lumogis_id: $lid, user_id: $uid, scope: $scope})",
            {"lid": lid, "uid": uid, "scope": scope},
        )

    def visible(uid: str, role: str = "user") -> set[tuple[str, str]]:
        frag, params = visible_cypher_fragment(_member(uid, role))
        rows = gs.query(
            f"MATCH (n:Entity) WHERE {frag} RETURN n.lumogis_id AS lid, n.scope AS scope",
            params,
        )
        return {(r["lid"], r["scope"]) for r in rows}

    shared_and_system = {("e-alice", "shared"), ("e-sys", "system")}
    assert visible(alice) == {("e-alice", "personal")} | shared_and_system
    assert visible(bob) == {("e-bob", "personal")} | shared_and_system
    assert visible(dana) == {("e-sys", "system")}  # opted out of shared
    assert visible(root, "admin") == shared_and_system  # no personal god-mode
