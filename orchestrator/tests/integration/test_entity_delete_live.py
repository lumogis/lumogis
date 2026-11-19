# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live Postgres proof of delete, Hide and the names Lumogis won't learn (LUM-966).

Real SQL. The unit tests fake the store and pin statement shape and order; the
cascades, the rule table's constraints and statement semantics, the extraction
path refusing a deleted name, two-member isolation and the Hide filter on every
Core read are proven here. Qdrant is the in-memory ``MockVectorStore`` (the arm
logic is what is under test; the adapter has its own suite). Members carry a
per-run suffix and are cleaned up afterwards.

Fails (does not skip) without Postgres under ``LUMOGIS_REQUIRE_INTEGRATION_DB=1``.
"""

# ruff: noqa: F811, E501  (fixtures imported from _kg_live are test parameters; SQL literals are long)

from __future__ import annotations

import random
import threading
import time
import uuid

import hooks
import pytest
from auth import UserContext
from models.entities import ExtractedEntity
from services.projection import projection_pk
from tests.conftest import MockEmbedder
from tests.conftest import MockVectorStore
from tests.integration._kg_live import _ent
from tests.integration._kg_live import _version
from tests.integration._kg_live import live_pg  # noqa: F401 — fixture
from tests.integration._kg_live import world  # noqa: F401 — fixture

import config
from services import entities
from services import entity_browser
from services import entity_deletions
from services import entity_edit
from services import entity_name_rules as enr
from services import wow_state

TABLES = (
    "entity_merges",
    "entity_relations",
    "entity_edges",
    "entity_name_rules",
    "entity_deletions",
    "entity_hidden",
    "known_distinct_entity_pairs",
)


@pytest.fixture
def env(world, monkeypatch):
    ms = world["ms"]
    vs = MockVectorStore()
    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setitem(config._instances, "embedder", MockEmbedder())
    monkeypatch.setattr("services.memory_purge._RETRY_BACKOFF_S", 0)
    config.set_effective_graph_mode_for_process("disabled")
    hooks.register(entity_deletions.Event.ENTITY_DELETED, entity_deletions.on_entity_deleted_points)
    world["vs"] = vs
    try:
        yield world
    finally:
        hooks.shutdown()
        config.set_effective_graph_mode_for_process(None)
        config.clear_graph_mode_env_cache()
        members = (world["alice"], world["bob"], world["nosharing"])
        for table in TABLES:
            ms.execute(f"DELETE FROM {table} WHERE user_id IN (%s, %s, %s)", members)  # noqa: S608


def _user(uid):
    return UserContext(user_id=uid, is_authenticated=True, role="user", allows_shared=True)


def _rel(ms, uid, entity, evidence, *, etype="DOCUMENT"):
    row = ms.fetch_one(
        "INSERT INTO entity_relations (source_id, relation_type, evidence_type, evidence_id, "
        "user_id) VALUES (%s, 'MENTIONED_IN_DOCUMENT', %s, %s, %s) RETURNING id",
        (entity, etype, evidence, uid),
    )
    return int(row["id"])


def _doc(ms, uid, path):
    row = ms.fetch_one(
        "INSERT INTO file_index (file_path, file_hash, file_type, chunk_count, user_id, scope) "
        "VALUES (%s, 'h', 'pdf', 1, %s, 'personal') RETURNING id",
        (path, uid),
    )
    return int(row["id"])


def _session(ms, uid, *entity_ids):
    sid = str(uuid.uuid4())
    ms.execute(
        "INSERT INTO sessions (session_id, user_id, entity_ids) VALUES (%s, %s, %s)",
        (sid, uid, [str(e) for e in entity_ids]),
    )
    return sid


def _edge(ms, uid, src, dst, rel="WORKS_WITH"):
    eid = uuid.uuid4().hex
    ms.execute(
        "INSERT INTO entity_edges (id, user_id, bank, src_entity_id, dst_entity_id, "
        "relation_type) VALUES (%s, %s, 'personal', %s, %s, %s)",
        (eid, uid, str(src), str(dst), rel),
    )
    return eid


def _share(ms, uid, source):
    """A shared projection of ``source``, the way ``project_entity`` writes it."""
    pk = projection_pk("entities", str(source), "shared")
    row = ms.fetch_one(
        "SELECT name, entity_type, aliases, context_tags, mention_count FROM entities "
        "WHERE entity_id = %s",
        (source,),
    )
    ms.execute(
        "INSERT INTO entities (entity_id, name, entity_type, aliases, context_tags, "
        "mention_count, user_id, scope, published_from, share_origin) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'shared', %s, 'user')",
        (
            pk,
            row["name"],
            row["entity_type"],
            row["aliases"],
            row["context_tags"],
            row["mention_count"],
            uid,
            str(source),
        ),
    )
    return pk


def _point(vs, **payload):
    vs.upsert("entities", str(uuid.uuid4()), [0.0], payload)


def _count(ms, sql, *params):
    return int(ms.fetch_one(sql, params)["n"])  # noqa: S608 — test-owned SQL


def _rules(ms, uid):
    return {
        (r["name_norm"], r["from_type"]): r
        for r in ms.fetch_all("SELECT * FROM entity_name_rules WHERE user_id = %s", (uid,))
    }


def _delete(env_, uid, entity_id):
    return entity_edit.delete_entry(_user(uid), entity_id, version=_version(env_["ms"], entity_id))


def _extract(uid, *pairs, evidence="doc-new"):
    return entities.store_entities(
        [ExtractedEntity(name=n, entity_type=k, extraction_quality=1.0) for n, k in pairs],
        evidence_id=evidence,
        evidence_type="DOCUMENT",
        user_id=uid,
    )


# ── L1: everything goes ──────────────────────────────────────────────────────


def test_L1_delete_removes_the_entry_everywhere_and_blocks_its_names(env):
    ms, vs, alice = env["ms"], env["vs"], env["alice"]
    eid = _ent(ms, "Total", alice, aliases=("TOTAL GmbH",), mention_count=3)
    other = _ent(ms, "Other", alice)
    docs = ["/docs/a.pdf", "/docs/b.pdf", "/docs/c.pdf"]
    for d in docs:
        _rel(ms, alice, eid, d)
    sid = _session(ms, alice, eid, other)
    _edge(ms, alice, eid, other)
    _edge(ms, alice, other, eid, rel="KNOWS")
    proj = _share(ms, alice, eid)
    _point(vs, entity_id=eid, user_id=alice)
    _point(vs, entity_id=proj, user_id=alice, published_from=eid)
    _point(vs, entity_id=other, user_id=alice)

    out = _delete(env, alice, eid)
    hooks.shutdown()  # waits for the background Qdrant clear

    assert out.household_copy_removed is True
    assert out.blocked_names == ["Total", "TOTAL GmbH"]
    assert (
        _count(ms, "SELECT count(*) AS n FROM entities WHERE entity_id IN (%s, %s)", eid, proj) == 0
    )
    assert _count(ms, "SELECT count(*) AS n FROM entity_relations WHERE source_id = %s", eid) == 0
    assert _count(ms, "SELECT count(*) AS n FROM entity_edges WHERE user_id = %s", alice) == 0
    ids = ms.fetch_one("SELECT entity_ids FROM sessions WHERE session_id = %s", (sid,))[
        "entity_ids"
    ]
    ids = [i for i in str(ids).strip("{}").split(",") if i] if not isinstance(ids, list) else ids
    assert ids == [str(other)]
    rules = _rules(ms, alice)
    assert set(rules) == {("total", "PERSON"), ("total gmbh", "PERSON")}
    assert all(r["action"] == "block" for r in rules.values())
    assert set(rules[("total", "PERSON")]["document_evidence"]) == set(docs)
    assert rules[("total gmbh", "PERSON")]["display_name"] == "TOTAL GmbH"
    # Qdrant: both filters empty, only the other entry's point remains.
    assert vs.count("entities") == 1
    tomb = ms.fetch_one(
        "SELECT *, projection_ids::text[] AS pids FROM entity_deletions WHERE entity_id = %s",
        (eid,),
    )
    assert tomb["points_cleared_at"] is not None
    assert tomb["graph_cleared_at"] is not None  # GRAPH_MODE=disabled: stamped at insert
    assert tomb["pids"] == [proj]
    assert _count(ms, "SELECT count(*) AS n FROM entities WHERE entity_id = %s", other) == 1


def test_L1b_a_delete_with_the_graph_enabled_leaves_the_graph_arm_for_the_graph(env):
    ms, alice = env["ms"], env["alice"]
    config.set_effective_graph_mode_for_process("service")
    eid = _ent(ms, "Total", alice)
    _delete(env, alice, eid)
    hooks.shutdown()
    tomb = ms.fetch_one("SELECT * FROM entity_deletions WHERE entity_id = %s", (eid,))
    assert tomb["points_cleared_at"] is not None and tomb["graph_cleared_at"] is None


# ── L2, L4, L5: a re-read does not bring it back; two members ────────────────


def test_L2_a_reread_after_a_delete_does_not_recreate_the_entry(env):
    ms, alice = env["ms"], env["alice"]
    eid = _ent(ms, "Total", alice, aliases=("TOTAL GmbH",))
    _delete(env, alice, eid)
    ids = _extract(alice, ("Total", "PERSON"), ("TOTAL GmbH", "PERSON"))
    assert ids == []
    assert (
        _count(
            ms,
            "SELECT count(*) AS n FROM entities WHERE user_id = %s AND name ILIKE 'total%%'",
            alice,
        )
        == 0
    )
    # The block is kind-scoped: the same name read as another kind is a different entry.
    ids = _extract(alice, ("Total", "ORG"))
    assert len(ids) == 1


def test_L4_one_members_block_does_not_stop_another_members_extraction(env):
    ms, alice, bob = env["ms"], env["alice"], env["bob"]
    _delete(env, alice, _ent(ms, "Total", alice))
    assert len(_extract(bob, ("Total", "PERSON"))) == 1


def test_L5_a_member_cannot_delete_read_or_remove_another_members_things(env):
    ms, alice, bob = env["ms"], env["alice"], env["bob"]
    personal = _ent(ms, "Secret", alice)
    shared_src = _ent(ms, "Public", alice)
    proj = _share(ms, alice, shared_src)
    with pytest.raises(entity_edit.EntityNotFound):  # N1: no existence oracle
        entity_edit.delete_entry(_user(bob), personal, version=1)
    with pytest.raises(entity_edit.EntityNotOwner):  # N2
        entity_edit.delete_entry(_user(bob), proj, version=1)
    assert (
        _count(ms, "SELECT count(*) AS n FROM entities WHERE entity_id IN (%s, %s)", personal, proj)
        == 2
    )
    _delete(env, alice, _ent(ms, "Total", alice))
    seen = entity_edit.list_name_rules(_user(bob))  # N3
    assert seen["blocked"] == [] and seen["blocked_total"] == 0
    with pytest.raises(entity_edit.NameRuleNotFound):  # N4
        entity_edit.remove_name_rule(
            _user(bob), name_norm="total", entity_type="PERSON", kind="block"
        )
    assert ("total", "PERSON") in _rules(ms, alice)
    assert len(entity_edit.list_name_rules(_user(alice))["blocked"]) == 1


# ── L3, L3b: merge records ───────────────────────────────────────────────────


def _merge(env_, uid, keep, other):
    ms = env_["ms"]
    return entity_edit.merge_entries(
        _user(uid), keep, other, version=_version(ms, keep), other_version=_version(ms, other)
    )


def test_L3_merge_records_naming_the_entry_go_with_it(env):
    ms, alice = env["ms"], env["alice"]
    w, lo, u1, u2 = (_ent(ms, n, alice) for n in ("Winner", "Loser", "Un", "Related"))
    _row, mid = _merge(env, alice, w, lo)
    entity_edit.undo_merge(_user(alice), mid)  # the loser is back; its (undone) record names it
    _merge(env, alice, w, lo)  # an open merge into the winner
    _merge(env, alice, u1, u2)  # unrelated
    before = _count(ms, "SELECT count(*) AS n FROM entity_merges WHERE user_id = %s", alice)
    assert before == 3
    out = _delete(env, alice, w)
    assert out.merges_removed == 2
    left = ms.fetch_all(
        "SELECT winner_id, loser_id FROM entity_merges WHERE user_id = %s", (alice,)
    )
    assert [(str(r["winner_id"]), str(r["loser_id"])) for r in left] == [(u1, u2)]


def test_L3b_undoing_a_merge_never_brings_back_an_edge_to_a_deleted_entry(env):
    ms, alice = env["ms"], env["alice"]
    w, lo, p = (_ent(ms, n, alice) for n in ("Winner", "Loser", "Project"))
    # P as the destination and as the source: the merge drops each duplicate into its record.
    _edge(ms, alice, w, p)
    _edge(ms, alice, lo, p)
    _edge(ms, alice, p, w, rel="KNOWS")
    _edge(ms, alice, p, lo, rel="KNOWS")
    _row, mid = _merge(env, alice, w, lo)
    dropped = ms.fetch_one(
        "SELECT jsonb_array_length(edges_dropped) AS n FROM entity_merges WHERE merge_id = %s",
        (mid,),
    )["n"]
    assert dropped == 2
    _delete(env, alice, p)
    restored, _survivor, _since, skipped = entity_edit.undo_merge(_user(alice), mid)
    assert restored["name"] == "Loser"
    assert skipped >= 2
    assert (
        _count(
            ms,
            "SELECT count(*) AS n FROM entity_edges WHERE user_id = %s AND (src_entity_id = %s OR dst_entity_id = %s)",
            alice,
            p,
            p,
        )
        == 0
    )


# ── L6: Hide ─────────────────────────────────────────────────────────────────


def test_L6_hide_removes_a_shared_entry_from_the_hiders_reads_only(env):
    ms, alice, bob, carol = env["ms"], env["alice"], env["bob"], env["nosharing"]
    src = _ent(ms, "Houseplan", alice, mention_count=5)
    proj = _share(ms, alice, src)
    bob_u = _user(bob)

    def bob_sees():
        listed = [e.entity_id for e in entity_browser.list_entities(bob_u).entities]
        counted = entity_browser.list_entities(bob_u).total
        found = entities.lookup_by_name("Houseplan", bob) is not None
        searched = bool(entities.search_by_name("Housepl", user_id=bob))
        wow = wow_state.count_visible_entities(bob)
        top = [e["name"] for e in wow_state.list_top_entities(bob)]
        return proj in listed, counted, found, searched, wow, "Houseplan" in top

    assert bob_sees() == (True, 1, True, True, 1, True)
    assert entity_edit.hide_entry(bob_u, proj) == src
    assert entity_edit.hide_entry(bob_u, proj) == src  # idempotent
    assert bob_sees() == (False, 0, False, False, 0, False)
    assert entity_edit.list_name_rules(bob_u)["hidden"][0]["entity_id"] == proj
    # Alice (the owner) and a third member still see it.
    assert [e.name for e in entity_browser.list_entities(_user(alice)).entities] == ["Houseplan"]
    assert entities.lookup_by_name("Houseplan", alice) is not None
    # Show again.
    entity_edit.unhide_entry(bob_u, proj)
    assert bob_sees() == (True, 1, True, True, 1, True)
    # The owner deleting the entry removes every hide of it.
    entity_edit.hide_entry(bob_u, proj)
    _delete(env, alice, src)
    assert _count(ms, "SELECT count(*) AS n FROM entity_hidden WHERE user_id = %s", bob) == 0
    with pytest.raises(entity_edit.EntityHideOwn):
        entity_edit.hide_entry(_user(alice), _ent(ms, "Mine", alice))
    assert carol  # (the opted-out member is only a bystander here)


# ── L7: concurrency ──────────────────────────────────────────────────────────


def test_L7_a_delete_waits_for_a_rename_in_flight_and_then_conflicts(env, monkeypatch):
    ms, alice = env["ms"], env["alice"]
    eid = _ent(ms, "Dr. Meier", alice)
    version = _version(ms, eid)
    in_txn = threading.Event()
    state = {"first": True}
    guard = threading.Lock()

    def hook():
        with guard:
            mine, state["first"] = state["first"], False
        if mine:
            in_txn.set()
            time.sleep(0.8)

    monkeypatch.setattr(entity_edit, "_before_commit", hook)
    out: dict = {}

    def rename():
        try:
            out["rename"] = entity_edit.rename_entity(
                _user(alice),
                eid,
                name="Dr. Meyer",
                aliases_add=[],
                aliases_remove=[],
                version=version,
            )
        except Exception as exc:  # noqa: BLE001
            out["rename"] = exc

    def delete():
        try:
            out["delete"] = entity_edit.delete_entry(_user(alice), eid, version=version)
        except Exception as exc:  # noqa: BLE001
            out["delete"] = exc

    ta, tb = threading.Thread(target=rename), threading.Thread(target=delete)
    ta.start()
    assert in_txn.wait(10)
    tb.start()
    ta.join(30)
    tb.join(30)
    assert isinstance(out["delete"], entity_edit.EntityVersionConflict)  # never both succeed
    assert isinstance(out["rename"], dict)
    assert _count(ms, "SELECT count(*) AS n FROM entities WHERE entity_id = %s", eid) == 1
    assert _rules(ms, alice) == {}


# ── L8: Qdrant down ──────────────────────────────────────────────────────────


def test_L8_a_qdrant_failure_does_not_fail_the_delete_and_the_sweep_finishes_it(env, monkeypatch):
    ms, vs, alice = env["ms"], env["vs"], env["alice"]
    eid = _ent(ms, "Total", alice)
    proj = _share(ms, alice, eid)
    _point(vs, entity_id=eid, user_id=alice)
    _point(vs, entity_id=proj, user_id=alice, published_from=eid)
    real = vs.delete_where
    failures = {"left": 3}  # one full retry budget

    def flaky(collection, filter):
        if failures["left"] > 0:
            failures["left"] -= 1
            raise RuntimeError("qdrant down")
        return real(collection, filter)

    monkeypatch.setattr(vs, "delete_where", flaky)
    out = _delete(env, alice, eid)  # returned without waiting on Qdrant
    assert out.blocked_names == ["Total"]
    hooks.shutdown()
    tomb = ms.fetch_one("SELECT * FROM entity_deletions WHERE entity_id = %s", (eid,))
    assert tomb["points_cleared_at"] is None and vs.count("entities") == 2
    ms.execute(
        "UPDATE entity_deletions SET deleted_at = NOW() - interval '1 hour' WHERE entity_id = %s",
        (eid,),
    )
    result = entity_deletions.sweep_entity_deletions()
    assert result["points"] >= 1
    tomb = ms.fetch_one("SELECT * FROM entity_deletions WHERE entity_id = %s", (eid,))
    assert tomb["points_cleared_at"] is not None and vs.count("entities") == 0


# ── L9: the rules round trip, and the invariant on the real table ────────────


def test_L9_removing_the_block_restores_the_kind_correction_and_offers_a_reread(env):
    ms, alice = env["ms"], env["alice"]
    docs = [(_doc(ms, alice, f"/docs/{n}.pdf"), f"/docs/{n}.pdf") for n in "abc"]
    org = _ent(ms, "Acme", alice, etype="PERSON")
    for _id, path in docs:
        _rel(ms, alice, org, path)
    # A kind correction PERSON -> ORG for "acme", then the entry (now ORG) is deleted.
    entity_edit.change_entity_kind(_user(alice), org, entity_type="ORG", version=_version(ms, org))
    rules = _rules(ms, alice)
    assert rules[("acme", "PERSON")]["action"] == "retype"
    _delete(env, alice, org)
    rules = _rules(ms, alice)
    assert (
        rules[("acme", "ORG")]["action"] == "block"
        and rules[("acme", "ORG")]["restore_to_type"] is None
    )
    assert (
        rules[("acme", "PERSON")]["action"] == "block"
        and rules[("acme", "PERSON")]["restore_to_type"] == "ORG"
    )
    assert _extract(alice, ("Acme", "PERSON"), ("Acme", "ORG")) == []  # both dropped
    # Listed once (the converted row is not addressable).
    listed = entity_edit.list_name_rules(_user(alice))
    assert [(b["name_norm"], b["entity_type"]) for b in listed["blocked"]] == [("acme", "ORG")]
    with pytest.raises(entity_edit.NameRuleNotFound):
        entity_edit.remove_name_rule(
            _user(alice), name_norm="acme", entity_type="PERSON", kind="block"
        )
    removed = entity_edit.remove_name_rule(
        _user(alice), name_norm="acme", entity_type="ORG", kind="block"
    )
    assert removed["restored_corrections"][0]["from_type"] == "PERSON"
    assert {d["document_id"] for d in removed["reread_documents"]} == {i for i, _p in docs}
    assert _rules(ms, alice)[("acme", "PERSON")]["action"] == "retype"
    ids = _extract(alice, ("Acme", "PERSON"))  # rewritten to ORG again
    assert (
        ms.fetch_one("SELECT entity_type FROM entities WHERE entity_id = %s", (ids[0],))[
            "entity_type"
        ]
        == "ORG"
    )


class _Rollback(Exception):
    pass


def test_L9b_the_three_invariants_hold_on_the_real_table_after_every_step(env):
    """200 seeded steps of retype / block / remove_block / lift_blocks, one rolled-back transaction."""
    ms, alice = env["ms"], env["alice"]
    kinds = ("PERSON", "ORG", "PROJECT", "CONCEPT")
    rng = random.Random(966)
    steps_1a = steps_2b = 0

    def converted_waiting_for(key, kind):
        return _count(
            ms,
            "SELECT count(*) AS n FROM entity_name_rules WHERE action = 'block' AND user_id = %s "
            "AND name_norm = %s AND restore_to_type = %s",
            alice,
            key,
            kind,
        )

    def check():
        bad = ms.fetch_one(
            "SELECT count(*) AS n FROM entity_name_rules r WHERE r.user_id = %s AND ("
            "  (r.action = 'retype' AND EXISTS (SELECT 1 FROM entity_name_rules o WHERE o.user_id = r.user_id "
            "     AND o.name_norm = r.name_norm AND o.from_type = r.to_type)) "
            "  OR (r.restore_to_type IS NOT NULL AND (r.restore_to_type = r.from_type OR NOT EXISTS ("
            "     SELECT 1 FROM entity_name_rules m WHERE m.user_id = r.user_id AND m.name_norm = r.name_norm "
            "     AND m.from_type = r.restore_to_type AND m.action = 'block' AND m.restore_to_type IS NULL))))",
            (alice,),
        )
        assert bad["n"] == 0, ms.fetch_all(
            "SELECT * FROM entity_name_rules WHERE user_id = %s", (alice,)
        )

    with pytest.raises(_Rollback):
        with ms.transaction():
            for _ in range(200):
                name = rng.choice(("Meier Pharmacy", "Total", "Acme"))
                key = enr.name_key(name)
                x, y = rng.sample(kinds, 2)
                op = rng.choice(("retype", "block", "remove_block", "lift_blocks"))
                if op == "retype":
                    # VERIFY-PLAN: count steps 1a (restore == to) and 2b (restore == from).
                    steps_1a += converted_waiting_for(key, y) > 0
                    steps_2b += converted_waiting_for(key, x) > 0
                    enr.record_retype(
                        ms,
                        user_id=alice,
                        name=name,
                        from_type=x,
                        to_type=y,
                        source_entity_id=str(uuid.uuid4()),
                    )
                elif op == "block":
                    existing = ms.fetch_one(
                        "SELECT action FROM entity_name_rules WHERE user_id = %s AND name_norm = %s AND from_type = %s",
                        (alice, key, x),
                    )
                    if existing and existing["action"] == "retype":
                        continue  # delete_entry skips a key with a retype starting at the kind
                    enr.record_block(
                        ms,
                        user_id=alice,
                        keys_and_names=[(key, name)],
                        entity_type=x,
                        source_entity_id=str(uuid.uuid4()),
                        document_evidence=[f"/d/{rng.randrange(9)}.pdf"],
                    )
                elif op == "remove_block":
                    enr.remove_block(ms, user_id=alice, name_norm=key, entity_type=x)
                else:
                    enr.lift_blocks(ms, user_id=alice, keys=[key], entity_type=x)
                check()
            raise _Rollback
    assert steps_1a > 0, "the walk never exercised record_retype step 1a"
    assert steps_2b > 0, "the walk never exercised record_retype step 2b"
    assert _rules(ms, alice) == {}  # rolled back


# ── L10: a rules outage on ingest re-reads later ─────────────────────────────


def test_L10_a_rules_outage_clears_the_hash_and_the_next_read_stores_entities(env, monkeypatch):
    from services import ingest

    ms, alice = env["ms"], env["alice"]
    path = f"/docs/lum966-{uuid.uuid4().hex[:6]}.pdf"
    _doc(ms, alice, path)
    ms.execute(
        "UPDATE file_index SET file_hash = 'abc' WHERE user_id = %s AND file_path = %s",
        (alice, path),
    )
    monkeypatch.setattr(
        "services.entities.extract_entities",
        lambda text, user_id=None, **kw: [
            ExtractedEntity(name="Newly Read", entity_type="ORG", extraction_quality=1.0)
        ],
    )
    monkeypatch.setattr("services.ingest.hooks.fire_background", lambda *a, **k: None)
    with monkeypatch.context() as m:
        m.setattr(
            enr, "load_rules", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rules down"))
        )
        out = ingest._emit_document_ingested_and_entities(
            logical_path=path, chunk_count_written=1, user_id=alice, text="x"
        )
    assert out == []
    row = ms.fetch_one(
        "SELECT file_hash, updated_at FROM file_index WHERE user_id = %s AND file_path = %s",
        (alice, path),
    )
    assert row["file_hash"] == "" and row["updated_at"].year == 1970
    assert (
        _count(
            ms,
            "SELECT count(*) AS n FROM entities WHERE user_id = %s AND name = 'Newly Read'",
            alice,
        )
        == 0
    )
    ingest._emit_document_ingested_and_entities(
        logical_path=path, chunk_count_written=1, user_id=alice, text="x"
    )
    assert (
        _count(
            ms,
            "SELECT count(*) AS n FROM entities WHERE user_id = %s AND name = 'Newly Read'",
            alice,
        )
        == 1
    )


# ── L11: an explicit name lifts the block; MCP endpoints respect it ──────────


def test_L11_rename_alias_and_kind_change_lift_a_block_and_mcp_endpoints_respect_one(
    env, monkeypatch
):
    from models.mcp_write import ExtractedRelation

    from services import mcp_write

    ms, alice = env["ms"], env["alice"]
    _delete(env, alice, _ent(ms, "Total", alice))
    assert ("total", "PERSON") in _rules(ms, alice)
    other = _ent(ms, "Dr. Meier", alice)
    entity_edit.rename_entity(
        _user(alice),
        other,
        name=None,
        aliases_add=["Total"],
        aliases_remove=[],
        version=_version(ms, other),
    )
    assert ("total", "PERSON") not in _rules(ms, alice)  # lifted by an explicit name
    assert entity_edit.list_name_rules(_user(alice))["blocked"] == []
    # Extraction is no longer dropped: it lands on an entry (matching is by name and
    # context, which is not what is under test here).
    assert len(_extract(alice, ("Total", "PERSON"))) == 1

    # A kind change onto a blocked name lifts it too.
    _delete(env, alice, _ent(ms, "Acme", alice, etype="ORG"))
    assert ("acme", "ORG") in _rules(ms, alice)
    holder = _ent(ms, "Holder", alice, etype="PERSON", aliases=("Acme",))
    entity_edit.change_entity_kind(
        _user(alice), holder, entity_type="ORG", version=_version(ms, holder)
    )
    assert ("acme", "ORG") not in _rules(ms, alice)

    # MCP add_memory: a relation endpoint naming a blocked CONCEPT creates no entry.
    _delete(env, alice, _ent(ms, "Budget", alice, etype="CONCEPT"))
    monkeypatch.setattr(mcp_write.memories, "store_memory", lambda **k: "mem1")
    monkeypatch.setattr(mcp_write, "extract_entities", lambda *a, **k: [])
    monkeypatch.setattr(
        mcp_write,
        "extract_relations",
        lambda *a, **k: [
            ExtractedRelation(src_name="Budget", dst_name="Plan", relation_type="DEPENDS_ON")
        ],
    )
    res = mcp_write.add_memory(user_id=alice, bank="personal", content="Budget depends on Plan")
    assert res["relation_ids"] == []
    assert (
        _count(
            ms, "SELECT count(*) AS n FROM entities WHERE user_id = %s AND name = 'Budget'", alice
        )
        == 0
    )
