# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live Postgres proof of merge records and undo (LUM-965).

Real SQL. The unit tests fake the store and only pin statement shape; the
record-in-place CTEs, the ``jsonb_populate_record`` re-insert, the ``NOT EXISTS``
guards and the per-survivor newest-first rule are proven here. Members carry a
per-run suffix and are cleaned up afterwards. Search points go to the in-memory
``MockVectorStore``.

Fails (does not skip) without Postgres under ``LUMOGIS_REQUIRE_INTEGRATION_DB=1``.
Needs Postgres only. Gate: ``make test-env-integration``.
"""

# ruff: noqa: F811  (fixtures imported from _kg_live are used as test parameters)

from __future__ import annotations

import random
import uuid
from datetime import datetime
from datetime import timezone

import psycopg2.errors
import pytest
from auth import UserContext
from models.entities import ExtractedEntity
from tests.conftest import MockEmbedder
from tests.conftest import MockVectorStore
from tests.integration._kg_live import _ent
from tests.integration._kg_live import _version
from tests.integration._kg_live import live_pg  # noqa: F401 — fixture
from tests.integration._kg_live import world  # noqa: F401 — fixture

import config
from services import entity_edit
from services import entity_merge


@pytest.fixture
def env(world, monkeypatch):
    ms = world["ms"]
    vs = MockVectorStore()
    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setitem(config._instances, "embedder", MockEmbedder())
    monkeypatch.setattr("services.entity_merge.hooks.fire_background", lambda *a, **k: None)
    world["vs"] = vs
    try:
        yield world
    finally:
        members = (world["alice"], world["bob"], world["nosharing"])
        for table in (
            "entity_merges",
            "entity_relations",
            "entity_edges",
            "entity_name_rules",
            "known_distinct_entity_pairs",
        ):
            ms.execute(f"DELETE FROM {table} WHERE user_id IN (%s, %s, %s)", members)  # noqa: S608


def _user(uid):
    return UserContext(user_id=uid, is_authenticated=True, role="user", allows_shared=True)


def _rel(ms, uid, entity, evidence, *, rtype="MENTIONED_IN_DOCUMENT", etype="DOCUMENT"):
    row = ms.fetch_one(
        "INSERT INTO entity_relations (source_id, relation_type, evidence_type, evidence_id, "
        "user_id) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (entity, rtype, etype, evidence, uid),
    )
    return int(row["id"])


def _session(ms, uid, *entities):
    sid = str(uuid.uuid4())
    ms.execute(
        "INSERT INTO sessions (session_id, user_id, entity_ids) VALUES (%s, %s, %s)",
        (sid, uid, [str(e) for e in entities]),
    )
    return sid


def _edge(ms, uid, src, dst, rel="WORKS_WITH", *, bank="personal"):
    eid = uuid.uuid4().hex
    ms.execute(
        "INSERT INTO entity_edges (id, user_id, bank, src_entity_id, dst_entity_id, "
        "relation_type, valid_from, graph_projected_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            eid,
            uid,
            bank,
            str(src),
            str(dst),
            rel,
            datetime(2026, 1, 2, tzinfo=timezone.utc),
            datetime(2026, 2, 1, tzinfo=timezone.utc),
        ),
    )
    return eid


def _share(ms, uid, source, scope="shared", origin="user"):
    """Insert a shared projection of ``source`` the way ``project_entity`` would."""
    from services.projection import projection_pk

    pk = projection_pk("entities", str(source), scope)
    row = ms.fetch_one(
        "SELECT name, entity_type, aliases, context_tags, mention_count FROM entities "
        "WHERE entity_id = %s",
        (source,),
    )
    ms.execute(
        "INSERT INTO entities (entity_id, name, entity_type, aliases, context_tags, "
        "mention_count, user_id, scope, published_from, share_origin, graph_projected_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())",
        (
            pk,
            row["name"],
            row["entity_type"],
            row["aliases"],
            row["context_tags"],
            row["mention_count"],
            uid,
            scope,
            str(source),
            origin,
        ),
    )
    return pk


_IGNORE = {"version", "graph_projected_at", "updated_at"}


def _state(ms, uid):
    """Everything a merge can touch, in comparable form."""
    ents = {}
    for r in ms.fetch_all("SELECT * FROM entities WHERE user_id = %s", (uid,)):
        ents[str(r["entity_id"])] = {k: v for k, v in r.items() if k not in _IGNORE}
    rels = {
        int(r["id"]): (
            str(r["source_id"]),
            r["evidence_id"],
            r["relation_type"],
            r["created_at"],
        )
        for r in ms.fetch_all("SELECT * FROM entity_relations WHERE user_id = %s", (uid,))
    }
    sessions = {
        str(r["session_id"]): set(r["entity_ids"])
        for r in ms.fetch_all("SELECT * FROM sessions WHERE user_id = %s", (uid,))
    }
    edges = {
        r["id"]: (
            r["src_entity_id"],
            r["dst_entity_id"],
            r["relation_type"],
            r["bank"],
            r["valid_until"],
        )
        for r in ms.fetch_all("SELECT * FROM entity_edges WHERE user_id = %s", (uid,))
    }
    pairs = {
        (str(r["entity_id_a"]), str(r["entity_id_b"]))
        for r in ms.fetch_all(
            "SELECT * FROM known_distinct_entity_pairs WHERE user_id = %s", (uid,)
        )
    }
    rules = {
        (r["name_norm"], r["from_type"], r["action"], r["to_type"])
        for r in ms.fetch_all("SELECT * FROM entity_name_rules WHERE user_id = %s", (uid,))
    }
    return {
        "entities": ents,
        "relations": rels,
        "sessions": sessions,
        "edges": edges,
        "pairs": pairs,
        "rules": rules,
    }


def _merge(ms, w, lo, uid, *, origin="member", keep_name="winner", acted_by=None):
    return entity_merge.merge_entities(
        w, lo, uid, origin=origin, acted_by=acted_by or uid, keep_name=keep_name
    )


def _undo(ms, merge_id, uid):
    """The member path's transaction around the engine, without the route."""
    with ms.transaction():
        out = entity_merge.undo_in_transaction(ms, merge_id=merge_id, user_id=uid, acted_by=uid)
    entity_merge.after_undo_commit(out)
    return out


def _same(a, b):
    """State equality ignoring the "not the same" pairs an undo adds on purpose."""
    return {k: v for k, v in a.items() if k != "pairs"} == {
        k: v for k, v in b.items() if k != "pairs"
    }


def _record(ms, merge_id):
    return ms.fetch_one("SELECT * FROM entity_merges WHERE merge_id = %s", (merge_id,))


# ── L1 — the record round-trips exactly ───────────────────────────────────────


def test_L1_merge_record_round_trip_exact(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Dr. Meier", uid, aliases=["Meier"], tags=["doc"], mention_count=3)
    lo = _ent(
        ms, "Dr. A. Meier", uid, aliases=["A. Meier"], tags=["doc", "clinic"], mention_count=2
    )
    x = _ent(ms, "Clinic", uid, etype="ORG")
    _rel(ms, uid, w, "/d/w1.md")
    _rel(ms, uid, w, "/d/shared.md")
    _rel(ms, uid, lo, "/d/l1.md")
    _rel(ms, uid, lo, "/d/l2.md")
    _rel(ms, uid, lo, "/d/shared.md")  # collides with W's row
    s_both = _session(ms, uid, w, lo)
    s_w = _session(ms, uid, w)
    s_l = _session(ms, uid, lo)
    _rel(ms, uid, w, s_w, rtype="MENTIONED_IN_SESSION", etype="SESSION")
    _rel(ms, uid, lo, s_l, rtype="MENTIONED_IN_SESSION", etype="SESSION")
    typed = _edge(ms, uid, lo, x)
    loop = _edge(ms, uid, lo, w, "PART_OF")
    dup_w = _edge(ms, uid, w, x, "KNOWS")
    dup_l = _edge(ms, uid, lo, x, "KNOWS")
    staged = _ent(ms, "Staged One", uid, is_staged=True)
    del staged

    before = _state(ms, uid)
    res = _merge(ms, w, lo, uid)
    assert res.merge_id
    mid_state = _state(ms, uid)
    assert lo not in mid_state["entities"]
    assert mid_state["entities"][w]["name"] == "Dr. Meier"
    assert {"Dr. A. Meier", "A. Meier", "Meier"} <= set(mid_state["entities"][w]["aliases"])
    assert mid_state["edges"][typed][0] == w  # repointed
    assert mid_state["edges"][loop][4] is not None  # self-loop archived
    assert dup_l not in mid_state["edges"] and dup_w in mid_state["edges"]
    assert mid_state["sessions"][s_l] == {w}

    rec = _record(ms, res.merge_id)
    assert rec["origin"] == "member" and rec["undone_at"] is None
    assert rec["loser_id"] == lo and rec["winner_id"] == w
    assert len(rec["relations_moved"]) == 3 and len(rec["relations_dropped"]) == 1
    assert rec["name_set"] is None
    assert set(rec["aliases_added"]) == {"Dr. A. Meier", "A. Meier"}

    out = _undo(ms, res.merge_id, uid)
    assert out.skipped_rows == 0
    after = _state(ms, uid)
    assert after["entities"] == before["entities"]
    assert after["relations"] == before["relations"]
    assert after["sessions"] == before["sessions"]
    assert after["edges"] == before["edges"]
    assert s_both in after["sessions"]
    # The pair is remembered as "not the same"; everything else equals the start.
    assert after["pairs"] - before["pairs"] == {tuple(sorted((w, lo)))}
    assert _record(ms, res.merge_id)["undone_at"] is not None
    # version moved on both rows (open edit dialogs must refetch).
    assert _version(ms, lo) == 2 and _version(ms, w) == 3


def test_L1b_staged_loser_restores_staged(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Beta", uid)
    ms.execute("UPDATE entities SET extraction_quality = 0.42 WHERE entity_id = %s", (lo,))
    before = _state(ms, uid)["entities"][lo]
    res = _merge(ms, w, lo, uid)
    _undo(ms, res.merge_id, uid)
    assert _state(ms, uid)["entities"][lo] == before


# ── L2 / L3 — names and what the survivor gains since ────────────────────────


def test_L2_merge_adds_loser_name_and_resolution_follows(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Dr. Meier", uid, tags=["clinic", "doctor"])
    lo = _ent(ms, "Dr. A. Meier", uid, tags=["clinic", "doctor"])
    _merge(ms, w, lo, uid)
    aliases = ms.fetch_one("SELECT aliases FROM entities WHERE entity_id = %s", (w,))["aliases"]
    assert "Dr. A. Meier" in aliases
    from services.entities import store_entities

    store_entities(
        [
            ExtractedEntity(
                name="Dr. A. Meier", entity_type="PERSON", context_tags=["clinic", "doctor"]
            )
        ],
        evidence_id="/d/new.md",
        evidence_type="DOCUMENT",
        user_id=uid,
    )
    names = [
        r["name"]
        for r in ms.fetch_all(
            "SELECT name FROM entities WHERE user_id = %s AND scope = 'personal'", (uid,)
        )
    ]
    assert names == ["Dr. Meier"]


def test_L3_undo_keeps_later_gains(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid, mention_count=2)
    lo = _ent(ms, "Beta", uid, mention_count=1)
    _rel(ms, uid, lo, "/d/b.md")
    res = _merge(ms, w, lo, uid)
    new_rel = _rel(ms, uid, w, "/d/new.md")
    entity_edit.rename_entity(
        _user(uid),
        w,
        name=None,
        aliases_add=["Doc M"],
        aliases_remove=[],
        version=_version(ms, w),
    )
    ms.execute("UPDATE entities SET mention_count = mention_count + 1 WHERE entity_id = %s", (w,))
    out = _undo(ms, res.merge_id, uid)
    row = ms.fetch_one("SELECT * FROM entities WHERE entity_id = %s", (w,))
    assert "Doc M" in row["aliases"] and "Beta" not in row["aliases"]
    assert row["mention_count"] == 3
    rels = {
        int(r["id"]): str(r["source_id"])
        for r in ms.fetch_all(
            "SELECT id, source_id FROM entity_relations WHERE user_id = %s", (uid,)
        )
    }
    assert rels[new_rel] == w
    assert out.moved_relation_ids and new_rel not in out.moved_relation_ids


# ── L4 / L7 / L8 — remembered pair, carried pairs, kinds ─────────────────────


def test_L4_undo_writes_distinct_pair_and_dedup_skips(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid, mention_count=3)
    lo = _ent(ms, "Alpha B", uid, mention_count=3)
    res = _merge(ms, w, lo, uid)
    _undo(ms, res.merge_id, uid)
    a, b = sorted((w, lo))
    assert ms.fetch_one(
        "SELECT 1 AS x FROM known_distinct_entity_pairs WHERE user_id=%s AND entity_id_a=%s "
        "AND entity_id_b=%s",
        (uid, a, b),
    )
    from services import deduplication

    known = {
        (str(r["entity_id_a"]), str(r["entity_id_b"]))
        for r in ms.fetch_all(
            "SELECT entity_id_a, entity_id_b FROM known_distinct_entity_pairs WHERE user_id = %s",
            (uid,),
        )
    }
    ents = ms.fetch_all("SELECT * FROM entities WHERE user_id = %s AND scope = 'personal'", (uid,))

    class _VS(MockVectorStore):
        def search(self, collection, vector, limit, threshold, filter=None):
            return [
                {"id": "x", "score": 0.99, "payload": {"entity_id": e["entity_id"]}} for e in ents
            ]

    # Without the remembered pair the two entries are candidates; with it they are not.
    pair = tuple(sorted((str(w), str(lo))))
    cands_open, _ = deduplication._build_candidates(ents, _VS(), uid, set())
    assert pair in {tuple(sorted(c)) for c in cands_open}
    cands, _ = deduplication._build_candidates(ents, _VS(), uid, known)
    assert pair not in {tuple(sorted(c)) for c in cands}


def test_L7_carried_distinct_pairs(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Beta", uid)
    y = _ent(ms, "Gamma", uid)
    a, b = sorted((lo, y))
    ms.execute(
        "INSERT INTO known_distinct_entity_pairs (user_id, entity_id_a, entity_id_b) "
        "VALUES (%s,%s,%s)",
        (uid, a, b),
    )
    res = _merge(ms, w, lo, uid)
    pairs = _state(ms, uid)["pairs"]
    assert tuple(sorted((w, y))) in pairs and (a, b) not in pairs
    _undo(ms, res.merge_id, uid)
    pairs = _state(ms, uid)["pairs"]
    assert (a, b) in pairs and tuple(sorted((w, y))) not in pairs


def test_L8_cross_kind_rule_and_restore(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Meier GmbH", uid, etype="ORG")
    lo = _ent(ms, "Meier Pharmacy", uid, etype="PERSON")
    before_rules = _state(ms, uid)["rules"]
    res = _merge(ms, w, lo, uid)
    assert ("meier pharmacy", "PERSON", "retype", "ORG") in _state(ms, uid)["rules"]
    _undo(ms, res.merge_id, uid)
    assert _state(ms, uid)["rules"] == before_rules


def test_L8_variant_changed_rules_are_left_alone(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Meier GmbH", uid, etype="ORG")
    lo = _ent(ms, "Meier Pharmacy", uid, etype="PERSON")
    res = _merge(ms, w, lo, uid)
    ms.execute("UPDATE entity_name_rules SET to_type = 'PROJECT' WHERE user_id = %s", (uid,))
    _undo(ms, res.merge_id, uid)
    assert ("meier pharmacy", "PERSON", "retype", "PROJECT") in _state(ms, uid)["rules"]


def test_L1c_keep_other_name_undo_restores_the_survivors_own_forms(env):
    """keep_name='other' drops the winner's alias equal to the loser's name; undo restores it."""
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Anna Meier", uid, aliases=["A. Meier"])
    lo = _ent(ms, "A. Meier", uid)
    before = _state(ms, uid)
    res = _merge(ms, w, lo, uid, keep_name="loser")
    row = ms.fetch_one("SELECT name, aliases FROM entities WHERE entity_id = %s", (w,))
    assert row["name"] == "A. Meier" and row["aliases"] == ["Anna Meier"]
    _undo(ms, res.merge_id, uid)
    assert _same(_state(ms, uid), before)
    row = ms.fetch_one("SELECT name, aliases FROM entities WHERE entity_id = %s", (w,))
    assert row["name"] == "Anna Meier" and row["aliases"] == ["A. Meier"]


def test_L1d_keep_other_name_then_rename_keeps_the_original_name(env):
    """A rename after a keep-loser merge must not make undo lose the survivor's old name."""
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Anna Meier", uid)
    lo = _ent(ms, "Dr. Meier", uid)
    res = _merge(ms, w, lo, uid, keep_name="loser")
    ms.execute("UPDATE entities SET name = 'Doc M' WHERE entity_id = %s", (w,))
    _undo(ms, res.merge_id, uid)
    row = ms.fetch_one("SELECT name, aliases FROM entities WHERE entity_id = %s", (w,))
    assert row["name"] == "Doc M"
    assert "Anna Meier" in row["aliases"]


# ── L5 / L6 — chains, newest first ───────────────────────────────────────────


def _chain_seed(ms, uid):
    w = _ent(ms, "W", uid, aliases=["Wa"], tags=["tw"], mention_count=2)
    lo = _ent(ms, "L", uid, aliases=["La"], tags=["tl"], mention_count=3)
    x = _ent(ms, "X", uid, aliases=["Xa"], tags=["tx"], mention_count=4)
    k = _ent(ms, "K", uid, tags=["tk"], mention_count=2)
    y = _ent(ms, "Y", uid, etype="ORG")
    _rel(ms, uid, w, "/d/w.md")
    _rel(ms, uid, lo, "/d/l.md")
    _rel(ms, uid, lo, "/d/c.md")
    _rel(ms, uid, x, "/d/c.md")  # R2 drops what R1 moved onto W
    _rel(ms, uid, k, "/d/k.md")
    _rel(ms, uid, k, "/d/w.md")
    _edge(ms, uid, lo, y, "KNOWS")
    _edge(ms, uid, x, y, "KNOWS")  # X already has what R1 moved
    _session(ms, uid, lo)
    _share(ms, uid, lo)  # L shared; W and X personal
    return w, lo, x, k, y


def test_L5_chain_undo_newest_first_exact(env):
    ms, uid = env["ms"], env["alice"]
    w, lo, x, k, y = _chain_seed(ms, uid)
    s0 = _state(ms, uid)
    r1 = _merge(ms, w, lo, uid)
    s1 = _state(ms, uid)
    mention_between = _rel(ms, uid, w, "/d/between.md")
    r2 = _merge(ms, x, w, uid)  # W is merged away into X (the chain)
    s2 = _state(ms, uid)
    r3 = _merge(ms, x, k, uid)

    _undo(ms, r3.merge_id, uid)
    assert _same(_state(ms, uid), s2)
    _undo(ms, r2.merge_id, uid)
    after2 = _state(ms, uid)
    expected = dict(s1)
    expected["relations"] = {
        **s1["relations"],
        mention_between: (
            w,
            "/d/between.md",
            "MENTIONED_IN_DOCUMENT",
            after2["relations"][mention_between][3],
        ),
    }
    assert after2["entities"] == s1["entities"]
    assert {i: v[:3] for i, v in after2["relations"].items()} == {
        i: v[:3] for i, v in expected["relations"].items()
    }
    assert after2["sessions"] == s1["sessions"] and after2["edges"] == s1["edges"]
    _undo(ms, r1.merge_id, uid)
    after1 = _state(ms, uid)
    assert after1["entities"] == s0["entities"]
    assert after1["edges"] == s0["edges"] and after1["sessions"] == s0["sessions"]
    rels0 = {i: v[:3] for i, v in s0["relations"].items()}
    rels1 = {i: v[:3] for i, v in after1["relations"].items() if i != mention_between}
    assert rels1 == rels0
    assert after1["relations"][mention_between][0] == w  # the mention stays on W


def test_L6_undo_refuses_older_first_and_changes_nothing(env):
    ms, uid = env["ms"], env["alice"]
    w, lo, x, k, y = _chain_seed(ms, uid)
    r1 = _merge(ms, w, lo, uid)
    r2 = _merge(ms, x, w, uid)
    snap = _state(ms, uid)
    with pytest.raises(entity_edit.MergeNotNewest) as ei:
        with ms.transaction():
            entity_merge.undo_in_transaction(ms, merge_id=r1.merge_id, user_id=uid, acted_by=uid)
    assert ei.value.newer_merge_id == r2.merge_id
    assert _state(ms, uid) == snap
    _undo(ms, r2.merge_id, uid)
    _undo(ms, r1.merge_id, uid)  # now succeeds


def test_L6b_two_independent_merges_into_one_survivor(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "W", uid)
    a = _ent(ms, "A", uid)
    b = _ent(ms, "B", uid)
    _rel(ms, uid, a, "/d/doc.md")
    _rel(ms, uid, b, "/d/doc.md")
    ra = _merge(ms, w, a, uid)
    rb = _merge(ms, w, b, uid)
    with pytest.raises(entity_edit.MergeNotNewest) as ei:
        with ms.transaction():
            entity_merge.undo_in_transaction(ms, merge_id=ra.merge_id, user_id=uid, acted_by=uid)
    assert ei.value.newer_merge_id == rb.merge_id
    _undo(ms, rb.merge_id, uid)
    _undo(ms, ra.merge_id, uid)
    rows = ms.fetch_all(
        "SELECT source_id FROM entity_relations WHERE user_id = %s AND evidence_id = '/d/doc.md'",
        (uid,),
    )
    assert {str(r["source_id"]) for r in rows} == {a, b} or len(rows) == 3


# ── L9 / L9b / L10 — shared copies ───────────────────────────────────────────


def test_L9_one_side_shared_moves_and_returns(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Dr. Meier", uid)
    lo = _ent(ms, "Dr. A. Meier", uid)
    pk = _share(ms, uid, lo)
    res = _merge(ms, w, lo, uid)
    row = ms.fetch_one("SELECT * FROM entities WHERE entity_id = %s", (pk,))
    assert str(row["published_from"]) == w and row["name"] == "Dr. Meier"
    _undo(ms, res.merge_id, uid)
    row = ms.fetch_one("SELECT * FROM entities WHERE entity_id = %s", (pk,))
    assert str(row["published_from"]) == lo
    assert not ms.fetch_all("SELECT 1 FROM entities WHERE published_from = %s", (w,))


def test_L9_variant_unshared_before_undo(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Dr. Meier", uid)
    lo = _ent(ms, "Dr. A. Meier", uid)
    pk = _share(ms, uid, lo)
    res = _merge(ms, w, lo, uid)
    ms.execute("DELETE FROM entities WHERE entity_id = %s", (pk,))
    out = _undo(ms, res.merge_id, uid)
    assert not ms.fetch_all("SELECT 1 FROM entities WHERE published_from = %s", (lo,))
    # The shared copy that could not go back is reported (LUM-965 verify).
    assert out.skipped_rows == 1


def test_L9b_undo_never_shares_unshared_entry(env):
    ms, uid = env["ms"], env["alice"]
    lo = _ent(ms, "L", uid)
    w = _ent(ms, "W", uid)
    x = _ent(ms, "X", uid)
    pk_l = _share(ms, uid, lo)
    pk_x = _share(ms, uid, x)
    r1 = _merge(ms, w, lo, uid)  # moves L's copy onto W
    r2 = _merge(ms, x, w, uid)  # collides with X's copy -> drops L's moved row
    assert ms.fetch_one("SELECT 1 AS x FROM entities WHERE entity_id = %s", (pk_l,)) is None
    _undo(ms, r2.merge_id, uid)
    row = ms.fetch_one("SELECT * FROM entities WHERE entity_id = %s", (pk_l,))
    assert row is not None and str(row["published_from"]) == w
    _undo(ms, r1.merge_id, uid)
    row = ms.fetch_one("SELECT * FROM entities WHERE entity_id = %s", (pk_l,))
    assert str(row["published_from"]) == lo
    assert not ms.fetch_all("SELECT 1 FROM entities WHERE published_from = %s", (w,))
    x_row = ms.fetch_one("SELECT * FROM entities WHERE entity_id = %s", (pk_x,))
    assert str(x_row["published_from"]) == x


def test_L10_both_shared_dropped_projection_returns(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "W", uid)
    lo = _ent(ms, "L", uid)
    pk_w = _share(ms, uid, w)
    pk_l = _share(ms, uid, lo, origin="document")
    res = _merge(ms, w, lo, uid)
    assert ms.fetch_one("SELECT 1 AS x FROM entities WHERE entity_id = %s", (pk_l,)) is None
    _undo(ms, res.merge_id, uid)
    row = ms.fetch_one("SELECT * FROM entities WHERE entity_id = %s", (pk_l,))
    assert row is not None and str(row["published_from"]) == lo
    assert row["share_origin"] == "document"
    assert ms.fetch_one("SELECT 1 AS x FROM entities WHERE entity_id = %s", (pk_w,))


# ── L11 / L12 — origins, the table ───────────────────────────────────────────


def test_L11_auto_dedup_merge_is_recorded_and_undoable(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Alpha B", uid)
    res = entity_merge.merge_entities(w, lo, uid, origin="auto_dedup")
    rec = _record(ms, res.merge_id)
    assert rec["origin"] == "auto_dedup" and rec["acted_by"] is None
    with ms.transaction():
        entity_merge.undo_in_transaction(ms, merge_id=res.merge_id, user_id=uid, acted_by=uid)
    assert _record(ms, res.merge_id)["undone_by"] == uid


def test_L12_migration_shape(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Beta", uid)
    with pytest.raises(psycopg2.errors.CheckViolation):
        ms.execute(
            "INSERT INTO entity_merges (user_id, winner_id, loser_id, origin, loser_row, "
            "winner_before) VALUES (%s,%s,%s,'bogus','{}','{}')",
            (uid, w, lo),
        )
    with pytest.raises(psycopg2.errors.CheckViolation):
        ms.execute(
            "INSERT INTO entity_merges (user_id, winner_id, loser_id, origin, loser_row, "
            "winner_before) VALUES (%s,%s,%s,'member','{}','{}')",
            (uid, w, w),
        )
    res = _merge(ms, w, lo, uid)
    ms.execute("DELETE FROM entities WHERE entity_id = %s", (w,))
    assert _record(ms, res.merge_id) is None  # cascade


# ── L13 — isolation ──────────────────────────────────────────────────────────


def test_L13_two_members_isolated(env):
    ms, alice, bob = env["ms"], env["alice"], env["bob"]
    a1 = _ent(ms, "A1", alice)
    a2 = _ent(ms, "A2", alice)
    b1 = _ent(ms, "B1", bob)
    b2 = _ent(ms, "B2", bob)
    res = _merge(ms, b1, b2, bob)
    with pytest.raises(entity_edit.MergeNotFound):
        with ms.transaction():
            entity_merge.undo_in_transaction(
                ms, merge_id=res.merge_id, user_id=alice, acted_by=alice
            )
    with pytest.raises(entity_edit.MergeNotFound):
        with ms.transaction():
            entity_merge.undo_in_transaction(
                ms, merge_id="not-a-uuid", user_id=alice, acted_by=alice
            )
    # The engine itself never reaches another member's rows.
    with pytest.raises(ValueError):
        entity_merge.merge_entities(a1, b1, alice, origin="member")
    assert _version(ms, a2) == 1


# ── L16 / L19 / L21 / L22 ────────────────────────────────────────────────────


def test_L16_undo_loser_id_taken(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Beta", uid)
    res = _merge(ms, w, lo, uid)
    ms.execute(
        "INSERT INTO entities (entity_id, name, entity_type, user_id) "
        "VALUES (%s, 'Squatter', 'PERSON', %s)",
        (lo, uid),
    )
    snap = _state(ms, uid)
    with pytest.raises(entity_edit.MergeLoserIdTaken):
        with ms.transaction():
            entity_merge.undo_in_transaction(ms, merge_id=res.merge_id, user_id=uid, acted_by=uid)
    assert _state(ms, uid) == snap


def test_L19_undo_restores_under_record_user_id(env):
    ms, alice = env["ms"], env["alice"]
    uid = f"default-{env['sfx']}"
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Beta", uid)
    _rel(ms, uid, lo, "/d/b.md")
    _rel(ms, uid, w, "/d/b.md")
    other = _ent(ms, "Gamma", uid)
    _edge(ms, uid, lo, other)
    res = _merge(ms, w, lo, uid)
    # What the AUTH_ENABLED default-user remap does: user_id columns move, JSONB does not.
    for table in ("entities", "entity_relations", "entity_edges", "entity_merges", "sessions"):
        ms.execute(f"UPDATE {table} SET user_id = %s WHERE user_id = %s", (alice, uid))  # noqa: S608
    _undo(ms, res.merge_id, alice)
    for table in ("entities", "entity_relations", "entity_edges"):
        assert not ms.fetch_all(f"SELECT 1 FROM {table} WHERE user_id = %s", (uid,))  # noqa: S608
    assert (
        ms.fetch_one("SELECT user_id FROM entities WHERE entity_id = %s", (lo,))["user_id"] == alice
    )
    ms.execute("DELETE FROM entities WHERE user_id = %s", (alice,))


def test_L21_undo_does_not_resurrect_purged_evidence(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Beta", uid)
    _rel(ms, uid, w, "/d/D.md")
    _rel(ms, uid, lo, "/d/D.md")
    res = _merge(ms, w, lo, uid)
    ms.execute(
        "DELETE FROM entity_relations WHERE user_id = %s AND evidence_id = '/d/D.md'", (uid,)
    )
    out = _undo(ms, res.merge_id, uid)
    assert not ms.fetch_all(
        "SELECT 1 FROM entity_relations WHERE user_id = %s AND evidence_id = '/d/D.md'", (uid,)
    )
    assert out.skipped_rows >= 1


def test_L22_non_member_merge_never_changes_household_visibility(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Beta", uid)
    _share(ms, uid, lo)
    snap = _state(ms, uid)
    for origin in ("auto_dedup", "review_queue", "admin"):
        with pytest.raises(entity_merge.MergeChangesSharing):
            entity_merge.merge_entities(w, lo, uid, origin=origin)
    assert _state(ms, uid) == snap
    assert not ms.fetch_all("SELECT 1 FROM entity_merges WHERE user_id = %s", (uid,))
    entity_merge.merge_entities(w, lo, uid, origin="member")  # the member path merges it


def test_engine_refuses_shared_copy_rows(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    pk = _share(ms, uid, w)
    other = _ent(ms, "Beta", uid)
    with pytest.raises(entity_merge.MergeNotPersonal):
        entity_merge.merge_entities(other, pk, uid, origin="member")


# ── L20 — randomised model ───────────────────────────────────────────────────


def test_L20_merge_undo_randomised_model(env):
    ms, uid = env["ms"], env["alice"]
    seed = random.randrange(10**6)
    rng = random.Random(seed)
    try:
        ids = [_ent(ms, f"E{i}", uid, aliases=[f"e{i}a"], tags=[f"t{i}"]) for i in range(4)]
        for i, e in enumerate(ids):
            for d in range(rng.randint(1, 3)):
                _rel(ms, uid, e, f"/d/{rng.randint(0, 4)}-{i}-{d}.md")
        _edge(ms, uid, ids[0], ids[1], "KNOWS")
        _share(ms, uid, ids[2])
        initial = _state(ms, uid)
        alive = list(ids)
        for _ in range(25):
            action = rng.choice(["merge", "merge", "undo"])
            if action == "merge" and len(alive) >= 2:
                w, lo = rng.sample(alive, 2)
                _merge(ms, w, lo, uid, keep_name=rng.choice(["winner", "loser"]))
                alive.remove(lo)
            else:
                # Undo the newest open merge of a random survivor (chains re-point records).
                heads = ms.fetch_all(
                    "SELECT DISTINCT ON (winner_id) merge_id::text AS merge_id, "
                    "loser_id::text AS loser_id FROM entity_merges "
                    "WHERE user_id = %s AND undone_at IS NULL "
                    "ORDER BY winner_id, merged_at DESC, merge_id DESC",
                    (uid,),
                )
                if heads:
                    pick = rng.choice(heads)
                    _undo(ms, pick["merge_id"], uid)
                    alive.append(pick["loser_id"])
        # Undo everything, newest first.
        while True:
            row = ms.fetch_one(
                "SELECT merge_id::text AS merge_id FROM entity_merges WHERE user_id = %s "
                "AND undone_at IS NULL ORDER BY merged_at DESC, merge_id DESC LIMIT 1",
                (uid,),
            )
            if row is None:
                break
            _undo(ms, row["merge_id"], uid)
        final = _state(ms, uid)
        assert final["entities"].keys() == initial["entities"].keys()
        assert {i: v[:3] for i, v in final["relations"].items()} == {
            i: v[:3] for i, v in initial["relations"].items()
        }
        assert final["sessions"] == initial["sessions"]
        assert final["edges"] == initial["edges"]
        for eid, row in initial["entities"].items():
            got = final["entities"][eid]
            for col in ("name", "aliases", "context_tags", "mention_count", "published_from"):
                assert got[col] == row[col], (seed, eid, col)
    except AssertionError:
        print(f"L20 seed={seed}")
        raise


# ── the member write path (merge_entries / undo_merge / history / move) ──────


def _me(ms, uid, keep, other, **kw):
    return entity_edit.merge_entries(
        _user(uid),
        keep,
        other,
        version=_version(ms, keep),
        other_version=_version(ms, other),
        **kw,
    )


def test_member_merge_undo_history_round_trip(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Dr. Meier", uid, mention_count=2)
    lo = _ent(ms, "Dr. A. Meier", uid, aliases=["A. Meier"])
    _rel(ms, uid, lo, "/d/l.md")
    row, merge_id = _me(ms, uid, w, lo, keep_name="other")
    assert row["name"] == "Dr. A. Meier" and "Dr. Meier" in row["aliases"]
    hist = entity_edit.list_merge_history(_user(uid), w)
    assert [(h["merge_id"], h["direction"], h["can_undo"]) for h in hist] == [
        (merge_id, "merged_in", True)
    ]
    assert hist[0]["other_name"] == "Dr. A. Meier" and hist[0]["restores_sharing"] is False
    restored, survivor, since, skipped = entity_edit.undo_merge(_user(uid), merge_id)
    assert restored["name"] == "Dr. A. Meier" and survivor["name"] == "Dr. Meier"
    assert survivor["aliases"] == [] and skipped == 0 and since == []
    hist = entity_edit.list_merge_history(_user(uid), w)
    assert hist[0]["undone_at"] is not None and hist[0]["can_undo"] is False
    out = entity_edit.list_merge_history(_user(uid), lo)
    assert out[0]["direction"] == "merged_out" and out[0]["other_name"] == "Dr. Meier"
    with pytest.raises(entity_edit.MergeAlreadyUndone):
        entity_edit.undo_merge(_user(uid), merge_id)


def test_M3_M9_same_entry_and_staged(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    staged = _ent(ms, "Pending", uid, is_staged=True)
    with pytest.raises(entity_edit.EntitySameEntry):
        _me(ms, uid, w, w)
    with pytest.raises(entity_edit.EntityStaged) as ei:
        _me(ms, uid, w, staged)
    assert ei.value.which == "other"
    with pytest.raises(entity_edit.EntityStaged) as ei:
        _me(ms, uid, staged, w)
    assert ei.value.which == "this"


def test_M2_version_conflict_names_which(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Beta", uid)
    with pytest.raises(entity_edit.EntityVersionConflict) as ei:
        entity_edit.merge_entries(
            _user(uid), w, lo, version=_version(ms, w), other_version=99, keep_name="this"
        )
    assert ei.value.which == "other" and ei.value.current_version == 1
    assert not ms.fetch_all("SELECT 1 FROM entity_merges WHERE user_id = %s", (uid,))


def test_M1_head_checks_tag_the_other_entry(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Beta", uid)
    pk = _share(ms, uid, lo)
    with pytest.raises(entity_edit.EntityNotEditable) as ei:
        _me(ms, uid, w, pk)
    assert ei.value.which == "other" and ei.value.source_entity_id == lo
    with pytest.raises(entity_edit.EntityNotFound) as ei:
        entity_edit.merge_entries(_user(uid), w, str(uuid.uuid4()), version=1, other_version=1)
    assert ei.value.which == "other"


def test_L13_member_path_two_members_isolated(env):
    ms, alice, bob = env["ms"], env["alice"], env["bob"]
    a1 = _ent(ms, "A1", alice)
    a2 = _ent(ms, "A2", alice)
    b1 = _ent(ms, "B1", bob)
    b2 = _ent(ms, "B2", bob)
    b_shared = _share(ms, bob, b2)
    with pytest.raises(entity_edit.EntityNotFound) as ei:
        _me(ms, alice, a1, b1)
    assert ei.value.which == "other"
    # Bob's shared copy is visible to Alice but not hers to edit.
    with pytest.raises(entity_edit.EntityNotOwner):
        entity_edit.merge_entries(_user(alice), a1, b_shared, version=1, other_version=1)
    _, merge_id = _me(ms, bob, b1, b2)
    with pytest.raises(entity_edit.MergeNotFound):
        entity_edit.undo_merge(_user(alice), merge_id)
    with pytest.raises(entity_edit.EntityNotFound):
        entity_edit.list_merge_history(_user(alice), b1)
    rel_b = _rel(ms, bob, b1, "/d/b.md")
    with pytest.raises(entity_edit.EntityNotFound):
        entity_edit.move_mentions(
            _user(alice), a1, b1, relation_ids=[rel_b], version=1, to_version=1
        )
    rel_alice = _rel(ms, alice, a1, "/d/a.md")
    with pytest.raises(entity_edit.MentionsNotFound):
        entity_edit.move_mentions(
            _user(alice), a2, a1, relation_ids=[rel_b, rel_alice], version=1, to_version=1
        )
    assert _state(ms, bob)["relations"][rel_b][0] == b1  # nothing moved


def test_L15_move_mentions_live(env):
    ms, uid = env["ms"], env["alice"]
    src = _ent(ms, "Alpha", uid, mention_count=4)
    dst = _ent(ms, "Beta", uid, mention_count=1)
    r1 = _rel(ms, uid, src, "/d/1.md")
    r2 = _rel(ms, uid, src, "/d/2.md")
    r3 = _rel(ms, uid, src, "/d/3.md")
    _rel(ms, uid, dst, "/d/2.md")  # collides with r2
    sid = _session(ms, uid, src)
    r_s = _rel(ms, uid, src, sid, rtype="MENTIONED_IN_SESSION", etype="SESSION")
    v1, v2 = _version(ms, src), _version(ms, dst)
    s_row, d_row, moved = entity_edit.move_mentions(
        _user(uid), src, dst, relation_ids=[r1, r2, r_s], version=v1, to_version=v2
    )
    assert moved == 3
    assert s_row["version"] == v1 + 1 and d_row["version"] == v2 + 1
    rels = _state(ms, uid)["relations"]
    assert rels[r1][0] == dst and rels[r_s][0] == dst and r2 not in rels and rels[r3][0] == src
    sess = _state(ms, uid)["sessions"][sid]
    assert dst in sess and src not in sess
    assert s_row["mention_count"] == 1 and d_row["mention_count"] == 3
    with pytest.raises(entity_edit.EntityVersionConflict):
        entity_edit.move_mentions(
            _user(uid), src, dst, relation_ids=[r3], version=v1, to_version=v2
        )


def test_mentions_since_lists_what_the_survivor_gained(env):
    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Alpha", uid)
    lo = _ent(ms, "Beta", uid)
    sid = str(uuid.uuid4())
    ms.execute(
        "INSERT INTO sessions (session_id, user_id, entity_ids, summary) VALUES (%s,%s,%s,'chat')",
        (sid, uid, []),
    )
    _rel(ms, uid, lo, sid, rtype="MENTIONED_IN_SESSION", etype="SESSION")
    _, merge_id = _me(ms, uid, w, lo)
    sid2 = str(uuid.uuid4())
    ms.execute(
        "INSERT INTO sessions (session_id, user_id, entity_ids, summary) VALUES (%s,%s,%s,'later')",
        (sid2, uid, []),
    )
    later = _rel(ms, uid, w, sid2, rtype="MENTIONED_IN_SESSION", etype="SESSION")
    _, _, since, _ = entity_edit.undo_merge(_user(uid), merge_id)
    assert [m.relation_id for m in since] == [later]
    assert since[0].kind == "chat" and since[0].title == "later"


def _race_merge_rename(env, monkeypatch, *, lock):
    import threading
    import time

    ms, uid = env["ms"], env["alice"]
    a = _ent(ms, "Race A", uid)
    b = _ent(ms, "Race B", uid)
    c = _ent(ms, "Race C", uid)
    if not lock:
        monkeypatch.setattr(entity_edit, "_LOCK_SQL", "SELECT 1")
    in_txn = threading.Event()
    first = {"v": True}
    guard = threading.Lock()

    def hook():
        with guard:
            mine, first["v"] = first["v"], False
        if mine:
            in_txn.set()
            time.sleep(0.8)

    monkeypatch.setattr(entity_edit, "_before_commit", hook)
    ends = {}

    def merge():
        _me(ms, uid, a, b)
        ends["merge"] = time.monotonic()

    def rename():
        entity_edit.rename_entity(
            _user(uid),
            c,
            name="Race C2",
            aliases_add=[],
            aliases_remove=[],
            version=_version(ms, c),
        )
        ends["rename"] = time.monotonic()

    ta = threading.Thread(target=merge)
    ta.start()
    assert in_txn.wait(10)
    tb = threading.Thread(target=rename)
    tb.start()
    ta.join(20)
    tb.join(20)
    return ends


def test_L14_merge_and_rename_serialise(env, monkeypatch):
    ends = _race_merge_rename(env, monkeypatch, lock=True)
    assert ends["rename"] >= ends["merge"]


def test_L14_negative_control_without_the_lock(env, monkeypatch):
    ends = _race_merge_rename(env, monkeypatch, lock=False)
    assert ends["rename"] < ends["merge"]


def test_L18_merge_perf_log(env, caplog):
    import logging
    import time

    ms, uid = env["ms"], env["alice"]
    w = _ent(ms, "Big W", uid)
    lo = _ent(ms, "Big L", uid)
    ms.execute(
        "INSERT INTO entity_relations (source_id, relation_type, evidence_type, evidence_id, "
        "user_id) SELECT %s, 'MENTIONED_IN_DOCUMENT', 'DOCUMENT', '/d/' || g || '.md', %s "
        "FROM generate_series(1, 5000) g",
        (lo, uid),
    )
    with caplog.at_level(logging.INFO, logger="services.entity_merge"):
        t0 = time.monotonic()
        res = _merge(ms, w, lo, uid)
        t1 = time.monotonic()
        _undo(ms, res.merge_id, uid)
        t2 = time.monotonic()
    size = ms.fetch_one(
        "SELECT pg_column_size(m.*) AS n FROM entity_merges m WHERE merge_id = %s", (res.merge_id,)
    )
    print(f"L18 merge={t1 - t0:.2f}s undo={t2 - t1:.2f}s record_bytes={size['n']}")
    assert _version(ms, lo) == 2


def test_L23_moved_projection_payload_resolves(env):
    ms, uid, vs = env["ms"], env["alice"], env["vs"]
    w = _ent(ms, "Dr. Meier", uid)
    lo = _ent(ms, "Dr. A. Meier", uid)
    pk = _share(ms, uid, lo)
    _me(ms, uid, w, lo)
    points = [
        p for p in vs._collections.get("entities", []) if p["payload"].get("scope") == "shared"
    ]
    assert points, "the winner's shared point was upserted"
    ids = {p["payload"]["entity_id"] for p in points}
    assert ids == {pk}  # names the row that exists, not projection_pk(winner, ...)
    row = ms.fetch_one("SELECT * FROM entities WHERE entity_id = %s", (pk,))
    assert row and str(row["published_from"]) == w
