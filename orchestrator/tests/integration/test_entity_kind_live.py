# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live proof of "correct what kind an entry is" (LUM-967).

Real Postgres, real SQL (migration 082 included). The unit tests fake the store
and cannot prove the collision query, the rule table's CHECK constraints, the
advisory lock or two-member isolation; this file does. The graph half (relabel in
place, one node, edges kept) is ``test_graph_entity_relabel_falkordb_live.py``.

Members and names carry a per-run suffix. Search points go to the in-memory
``MockVectorStore`` (a real Qdrant round trip adds nothing that
``personal_entity_point``'s own tests do not cover); the embedder records the text
it was asked to embed. It must fail, not skip, without Postgres (the ``live_pg``
fixture's behaviour — do not add a skip).

Needs Postgres only. Gate: ``make test-env-integration``.
"""

# ruff: noqa: F811  (fixtures imported from _kg_live are used as test parameters)

from __future__ import annotations

import random

import psycopg2.errors
import pytest
from models.entities import ExtractedEntity
from services.entities import store_entities
from tests.conftest import MockEmbedder
from tests.conftest import MockVectorStore
from tests.integration._kg_live import _as
from tests.integration._kg_live import _ent
from tests.integration._kg_live import _race
from tests.integration._kg_live import _version
from tests.integration._kg_live import kg_app  # noqa: F401 — fixture
from tests.integration._kg_live import live_pg  # noqa: F401 — fixture
from tests.integration._kg_live import world  # noqa: F401 — fixture

import config
from services import entity_edit
from services import entity_name_rules
from services import entity_quality

MISSING = "00000000-0000-4000-8000-000000000000"


class _RecordingEmbedder(MockEmbedder):
    def __init__(self):
        self.texts: list[str] = []

    def embed(self, text):
        self.texts.append(text)
        return super().embed(text)


@pytest.fixture
def env(world, monkeypatch):
    """``world`` + a recording embedder, no hooks, a pass-through quality gate, and
    the members' rules removed afterwards."""
    ms = world["ms"]
    emb = _RecordingEmbedder()
    vs = MockVectorStore()
    monkeypatch.setitem(config._instances, "embedder", emb)
    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setattr("services.entities.hooks.fire_background", lambda *a, **k: None)

    def _gate(entities, user_id):
        for e in entities:
            e.extraction_quality, e.is_staged = 1.0, False
        return entities, 0

    monkeypatch.setattr(entity_quality, "score_and_filter_entities", _gate)
    world["emb"], world["vs"] = emb, vs
    try:
        yield world
    finally:
        members = (world["alice"], world["bob"], world["nosharing"])
        ms.execute("DELETE FROM entity_relations WHERE user_id IN (%s, %s, %s)", members)
        ms.execute("DELETE FROM entity_name_rules WHERE user_id IN (%s, %s, %s)", members)


def _rules(ms, user, sfx):
    rows = ms.fetch_all(
        "SELECT name_norm, from_type, action, to_type FROM entity_name_rules "
        "WHERE user_id = %s AND name_norm LIKE %s ORDER BY name_norm, from_type",
        (user, f"%{sfx}"),
    )
    return {(r["name_norm"], r["from_type"]): (r["action"], r["to_type"]) for r in rows}


def _row(ms, eid):
    return ms.fetch_one(
        "SELECT entity_type, version, graph_projected_at, aliases, name FROM entities "
        "WHERE entity_id = %s",
        (eid,),
    )


def _patch(client, eid, version, **body):
    return client.patch(f"/api/v1/kg/entities/{eid}", json={"version": version, **body})


def _reread(name, kind, user, *, tags=(), ev="/d/x.pdf"):
    return store_entities(
        [ExtractedEntity(name=name, entity_type=kind, context_tags=list(tags))],
        evidence_id=ev,
        evidence_type="DOCUMENT",
        user_id=user,
    )


# ── the change itself ────────────────────────────────────────────────────────


def test_change_kind_end_to_end(env, kg_app, monkeypatch):
    ms, sfx, alice = env["ms"], env["sfx"], env["alice"]
    name = f"Meier Pharmacy {sfx}"
    eid = _ent(ms, name, alice, aliases=[f"Meier Apotheke {sfx}"], tags=["pharmacy", "street"])
    ms.execute(
        "INSERT INTO entity_relations (source_id, relation_type, evidence_type, evidence_id, "
        "user_id) VALUES (%s, 'MENTIONED_IN_DOCUMENT', 'DOCUMENT', %s, %s)",
        (eid, f"/data/{sfx}/a.md", alice),
    )
    ms.execute("UPDATE entities SET graph_projected_at = NOW() WHERE entity_id = %s", (eid,))
    v = _version(ms, eid)
    client = _as(kg_app, monkeypatch, alice)

    resp = _patch(client, eid, v, entity_type="ORG")
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "ORG" and resp.json()["version"] == v + 1

    row = _row(ms, eid)
    assert (row["entity_type"], row["version"], row["graph_projected_at"]) == ("ORG", v + 1, None)
    assert row["aliases"] == [f"Meier Apotheke {sfx}"] and row["name"] == name
    rel = ms.fetch_one("SELECT count(*) AS n FROM entity_relations WHERE source_id = %s", (eid,))
    assert rel["n"] == 1
    assert _rules(ms, alice, sfx) == {(f"meier pharmacy {sfx}", "PERSON"): ("retype", "ORG")}

    def _names(kind):
        r = client.get("/api/v1/kg/entities", params={"kind": kind, "limit": 200})
        return [e["name"] for e in r.json()["entities"] if sfx in e["name"]]

    assert _names("org") == [name] and _names("person") == []

    # the search point follows: re-embedded with the new kind, payload carries it
    points = [
        p for p in env["vs"]._collections.get("entities", []) if p["payload"]["entity_id"] == eid
    ]
    assert points and points[-1]["payload"]["entity_type"] == "ORG"
    assert any("(ORG)" in t and name in t for t in env["emb"].texts)


def test_reread_without_tag_overlap_keeps_corrected_kind(env, kg_app, monkeypatch):
    ms, sfx, alice = env["ms"], env["sfx"], env["alice"]
    name = f"Meier Pharmacy {sfx}"
    eid = _ent(ms, name, alice, tags=["pharmacy", "street"])
    assert (
        _patch(
            _as(kg_app, monkeypatch, alice), eid, _version(ms, eid), entity_type="ORG"
        ).status_code
        == 200
    )

    _reread(name, "PERSON", alice, tags=["gardening"])

    def _count(kind):
        return ms.fetch_one(
            "SELECT count(*) AS n FROM entities WHERE user_id = %s AND name = %s "
            "AND entity_type = %s AND scope = 'personal'",
            (alice, name, kind),
        )["n"]

    assert _count("PERSON") == 0
    # Today's no-overlap extraction behaviour (a duplicate, not a merge; merging
    # is LUM-965) — now of the corrected kind. Pinned so it cannot hide.
    assert _count("ORG") == 2


def test_reread_with_tag_overlap_merges_into_corrected_entry(env, kg_app, monkeypatch):
    ms, sfx, alice = env["ms"], env["sfx"], env["alice"]
    name = f"Meier Pharmacy {sfx}"
    eid = _ent(ms, name, alice, tags=["pharmacy", "street"])
    _patch(_as(kg_app, monkeypatch, alice), eid, _version(ms, eid), entity_type="ORG")

    ids = _reread(name, "PERSON", alice, tags=["pharmacy", "street", "x"])
    assert ids == [eid]
    row = ms.fetch_one(
        "SELECT entity_type, mention_count FROM entities WHERE entity_id = %s", (eid,)
    )
    assert row["entity_type"] == "ORG" and row["mention_count"] == 2


def test_mcp_explicit_kind_wins(env, kg_app, monkeypatch):
    ms, sfx, alice = env["ms"], env["sfx"], env["alice"]
    name = f"Meier Pharmacy {sfx}"
    eid = _ent(ms, name, alice)
    _patch(_as(kg_app, monkeypatch, alice), eid, _version(ms, eid), entity_type="ORG")

    ids = store_entities(
        [ExtractedEntity(name=f"Meier Pharmacy 2 {sfx}", entity_type="PERSON")],
        evidence_id=f"mcp-{sfx}",
        evidence_type="MEMORY",
        user_id=alice,
        skip_quality_gate=True,
    )
    assert _row(ms, ids[0])["entity_type"] == "PERSON"
    # an explicit write for the corrected name itself is not rewritten either (no
    # shared context, so it is a separate entry — of the kind the caller asserted)
    ids = store_entities(
        [ExtractedEntity(name=name, entity_type="PERSON", context_tags=["fresh"])],
        evidence_id=f"mcp2-{sfx}",
        evidence_type="MEMORY",
        user_id=alice,
        skip_quality_gate=True,
    )
    assert ids[0] != eid and _row(ms, ids[0])["entity_type"] == "PERSON"
    assert _row(ms, eid)["entity_type"] == "ORG"


def test_two_members_rules_isolated(env, kg_app, monkeypatch):
    ms, sfx, alice, bob = env["ms"], env["sfx"], env["alice"], env["bob"]
    name = f"Meier Pharmacy {sfx}"
    a = _ent(ms, name, alice)
    b = _ent(ms, name, bob)
    assert (
        _patch(_as(kg_app, monkeypatch, alice), a, _version(ms, a), entity_type="ORG").status_code
        == 200
    )
    assert _row(ms, b)["entity_type"] == "PERSON"
    assert _rules(ms, bob, sfx) == {}

    ids = _reread(name, "PERSON", bob, tags=["z"])
    assert _row(ms, ids[0])["entity_type"] == "PERSON"

    # Bob PATCHing Alice's entry: another member's personal entry is invisible.
    r = _patch(_as(kg_app, monkeypatch, bob), a, _version(ms, a), entity_type="PROJECT")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "entity_not_found"

    # Bob's ORG twin never collides with, or appears in the 409 body of, Alice's change.
    c = _ent(ms, f"Twin {sfx}", alice)
    _ent(ms, f"Twin {sfx}", bob, etype="ORG")
    ok = _patch(_as(kg_app, monkeypatch, alice), c, _version(ms, c), entity_type="ORG")
    assert ok.status_code == 200, ok.text


def test_projection_rows_refuse_and_follow(env, kg_app, monkeypatch):
    ms, sfx, alice, bob = env["ms"], env["sfx"], env["alice"], env["bob"]
    name = f"Meier Pharmacy {sfx}"
    src = _ent(ms, name, alice)
    proj = _ent(ms, name, alice, scope="shared", published_from=src)
    ms.execute("UPDATE entities SET graph_projected_at = NOW() WHERE entity_id = %s", (proj,))
    client = _as(kg_app, monkeypatch, alice)

    r = _patch(client, proj, 1, entity_type="ORG")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "not_editable"
    assert r.json()["detail"]["source_entity_id"] == src

    assert _patch(client, src, _version(ms, src), entity_type="ORG").status_code == 200
    prow = _row(ms, proj)
    assert prow["entity_type"] == "ORG" and prow["graph_projected_at"] is None

    # Bob sees Alice's shared projection but may not edit it.
    r = _patch(_as(kg_app, monkeypatch, bob), proj, 1, entity_type="PROJECT")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_owner"


def test_target_kind_collision_409_and_no_rule(env, kg_app, monkeypatch):
    ms, sfx, alice = env["ms"], env["sfx"], env["alice"]
    name = f"Meier Pharmacy {sfx}"
    person = _ent(ms, name, alice)
    org = _ent(ms, name, alice, etype="ORG")
    _ent(ms, name, alice, etype="PROJECT")  # a PROJECT twin does not block a change to ORG
    client = _as(kg_app, monkeypatch, alice)

    r = _patch(client, person, _version(ms, person), entity_type="ORG")
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "name_in_use"
    assert d["conflicting_entity_id"] == org and d["conflicting_entity_type"] == "ORG"
    assert d["conflicting_name"] == name
    assert _rules(ms, alice, sfx) == {}
    assert _row(ms, person)["entity_type"] == "PERSON"

    ok = _patch(client, person, _version(ms, person), entity_type="CONCEPT")
    assert ok.status_code == 200  # no CONCEPT twin


def test_invalid_kind_and_one_change_at_a_time(env, kg_app, monkeypatch):
    ms, sfx, alice = env["ms"], env["sfx"], env["alice"]
    eid = _ent(ms, f"Meier {sfx}", alice)
    client = _as(kg_app, monkeypatch, alice)
    for bad in ("PLACE", "CODING_DECISION"):
        r = _patch(client, eid, 1, entity_type=bad)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_kind"
    r = _patch(client, eid, 1, entity_type="ORG", name="X")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "one_change_at_a_time"
    r = _patch(client, eid, 99, entity_type="ORG")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "version_conflict"
    # same kind (any case): no write, version unchanged
    r = _patch(client, eid, 1, entity_type="person")
    assert r.status_code == 200 and r.json()["version"] == 1


# ── the rule table ───────────────────────────────────────────────────────────


def test_rule_invariant_live(env, kg_app, monkeypatch):
    ms, sfx, alice = env["ms"], env["sfx"], env["alice"]
    client = _as(kg_app, monkeypatch, alice)
    k = f"meier pharmacy {sfx}"

    e = _ent(ms, f"Meier Pharmacy {sfx}", alice)
    for kind in ("ORG", "PERSON"):  # change back
        assert _patch(client, e, _version(ms, e), entity_type=kind).status_code == 200
    assert _rules(ms, alice, sfx) == {(k, "ORG"): ("retype", "PERSON")}

    ms.execute("DELETE FROM entity_name_rules WHERE user_id = %s", (alice,))
    for kind in ("PROJECT", "ORG"):  # chain
        assert _patch(client, e, _version(ms, e), entity_type=kind).status_code == 200
    assert _rules(ms, alice, sfx) == {
        (k, "PERSON"): ("retype", "ORG"),
        (k, "PROJECT"): ("retype", "ORG"),
    }

    # two same-name entries of different kinds carry independent rules
    ms.execute("DELETE FROM entity_name_rules WHERE user_id = %s", (alice,))
    ms.execute("DELETE FROM entities WHERE entity_id = %s", (e,))
    e1 = _ent(ms, f"Meier Pharmacy {sfx}", alice)
    e2 = _ent(ms, f"Meier Pharmacy {sfx}", alice, etype="PROJECT")
    assert _patch(client, e1, _version(ms, e1), entity_type="ORG").status_code == 200
    assert _patch(client, e2, _version(ms, e2), entity_type="CONCEPT").status_code == 200
    assert _rules(ms, alice, sfx) == {
        (k, "PERSON"): ("retype", "ORG"),
        (k, "PROJECT"): ("retype", "CONCEPT"),
    }


def test_rule_invariant_randomised_live(env):
    ms, sfx, alice = env["ms"], env["sfx"], env["alice"]
    rng = random.Random(967)
    kinds = ("PERSON", "ORG", "PROJECT", "CONCEPT")
    name = f"Meier Pharmacy {sfx}"
    for _ in range(200):
        x, y = rng.sample(kinds, 2)
        with ms.transaction():
            entity_name_rules.record_retype(
                ms, user_id=alice, name=name, from_type=x, to_type=y, source_entity_id=MISSING
            )
        rules = _rules(ms, alice, sfx)
        froms = {k[1] for k in rules}
        for (_, frm), (_, to) in rules.items():
            assert frm != to and to not in froms and frm != y, rules


def test_migration_constraints(env):
    ms, alice = env["ms"], env["alice"]
    assert ms.fetch_one("SELECT to_regclass('entity_name_rules') AS t")["t"] is not None
    bad = [
        ("block", "ORG", "PERSON"),  # LUM-966 widens the action CHECK
        ("retype", None, "PERSON"),  # a retype needs a target
        ("retype", "PERSON", "PERSON"),  # no self-rule
    ]
    for action, to_type, from_type in bad:
        with pytest.raises(psycopg2.errors.CheckViolation):
            ms.execute(
                "INSERT INTO entity_name_rules (user_id, name_norm, from_type, action, to_type) "
                "VALUES (%s, %s, %s, %s, %s)",
                (alice, "x-bad", from_type, action, to_type),
            )
    with pytest.raises(psycopg2.errors.CheckViolation):
        ms.execute(
            "INSERT INTO entity_name_rules (user_id, name_norm, from_type, action, to_type) "
            "VALUES (%s, '', 'PERSON', 'retype', 'ORG')",
            (alice,),
        )


# ── concurrency ──────────────────────────────────────────────────────────────


def _two_same_name_entries(ms, sfx, alice):
    e1 = _ent(ms, f"Meier {sfx}", alice)
    e2 = _ent(ms, f"Meier {sfx}", alice, etype="PROJECT")
    return e1, e2


def _body(ms):
    return lambda eid: {"entity_type": "ORG", "version": _version(ms, eid)}


def _org_count(ms, alice, sfx):
    return ms.fetch_one(
        "SELECT count(*) AS n FROM entities WHERE user_id = %s AND name = %s "
        "AND entity_type = 'ORG' AND scope = 'personal'",
        (alice, f"Meier {sfx}"),
    )["n"]


def test_concurrent_kind_changes_serialise(env, kg_app, monkeypatch):
    ms, sfx, alice = env["ms"], env["sfx"], env["alice"]
    e1, e2 = _two_same_name_entries(ms, sfx, alice)
    codes = _race(kg_app, ms, monkeypatch, alice, e1, e2, body=_body(ms))
    assert codes == (200, 409)
    assert _org_count(ms, alice, sfx) == 1


def test_concurrent_kind_changes_negative_control_without_lock(env, kg_app, monkeypatch):
    """Same race with the advisory lock removed: both succeed and two ORG entries
    named alike exist — so the lock is what serialises them."""
    ms, sfx, alice = env["ms"], env["sfx"], env["alice"]
    e1, e2 = _two_same_name_entries(ms, sfx, alice)
    monkeypatch.setattr(entity_edit, "_LOCK_SQL", "SELECT %s::integer, %s")
    codes = _race(kg_app, ms, monkeypatch, alice, e1, e2, body=_body(ms))
    assert codes == (200, 200)
    assert _org_count(ms, alice, sfx) == 2
