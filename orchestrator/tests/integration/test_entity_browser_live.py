# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live two-member proof of "What Lumogis knows" (LUM-161).

Real Postgres, real SQL. The unit tests mock the service layer and cannot prove
isolation; **this file is the privacy gate** for the entity browser: a member
sees their own entries and the household-shared ones, never another member's
personal entries, and the "Mentioned in" list holds only items the viewer can
open. It must fail, not skip, when Postgres is unreachable (the ``live_pg``
fixture's behaviour — do not add a skip).

Members have no ``users`` rows (so ``allows_shared`` resolves ``True``) except
the one ``allows_shared=false`` member in the deny tests. Names carry a per-run
suffix and assertions filter on it, because other suites leave shared entries in
the same database.

Needs Postgres only. Gate: ``make test-env-integration``.
"""

# ruff: noqa: F811  (fixtures imported from _kg_live are used as test parameters)

from __future__ import annotations

import uuid

from models.entities import ExtractedEntity
from tests.integration._kg_live import _as
from tests.integration._kg_live import _ent
from tests.integration._kg_live import _race
from tests.integration._kg_live import _version
from tests.integration._kg_live import graph_mode  # noqa: F401 — fixture
from tests.integration._kg_live import kg_app  # noqa: F401 — fixture
from tests.integration._kg_live import live_pg  # noqa: F401 — fixture
from tests.integration._kg_live import world  # noqa: F401 — fixture

from services import entity_edit

MISSING = "00000000-0000-4000-8000-000000000000"


# ── seeding ──────────────────────────────────────────────────────────────────


def _doc(ms, owner, path, *, scope="personal", published_from=None) -> int:
    row = ms.fetch_one(
        "INSERT INTO file_index (file_path, file_hash, file_type, chunk_count, user_id, "
        "scope, published_from) VALUES (%s, %s, '.md', 1, %s, %s, %s) RETURNING id",
        (path, "h-" + uuid.uuid4().hex, owner, scope, published_from),
    )
    return int(row["id"])


def _session(ms, owner, summary, *, scope="personal", published_from=None) -> str:
    sid = str(uuid.uuid4())
    ms.execute(
        "INSERT INTO sessions (session_id, summary, user_id, scope, published_from) "
        "VALUES (%s, %s, %s, %s, %s)",
        (sid, summary, owner, scope, published_from),
    )
    return sid


_REL = {
    "DOCUMENT": "MENTIONED_IN_DOCUMENT",
    "SESSION": "MENTIONED_IN_SESSION",
    "MEMORY": "MENTIONED_IN_MEMORY",
}


def _relate(ms, entity_id, owner, evidence_type, evidence_id) -> None:
    ms.execute(
        "INSERT INTO entity_relations (source_id, relation_type, evidence_type, evidence_id, "
        "user_id) VALUES (%s, %s, %s, %s, %s)",
        (entity_id, _REL[evidence_type], evidence_type, evidence_id, owner),
    )


def _names(resp, sfx):
    return sorted(e["name"] for e in resp.json()["entities"] if sfx in e["name"])


# ── list: isolation (the privacy gate) ───────────────────────────────────────


def test_bob_sees_own_and_shared_never_alices_personal(world, kg_app, monkeypatch, graph_mode):  # noqa: F811
    ms, sfx = world["ms"], world["sfx"]
    meier = _ent(ms, f"Dr. Meier {sfx}", world["alice"], mention_count=5)
    _ent(ms, f"Dr. Meier {sfx}", world["alice"], scope="shared", published_from=meier)
    _ent(ms, f"Secret Project {sfx}", world["alice"], etype="PROJECT")
    _ent(ms, f"Bob's Garage {sfx}", world["bob"], etype="ORG")

    bob = _as(kg_app, monkeypatch, world["bob"]).get("/api/v1/kg/entities", params={"limit": 200})
    assert bob.status_code == 200
    assert _names(bob, sfx) == [f"Bob's Garage {sfx}", f"Dr. Meier {sfx}"]
    shared = next(e for e in bob.json()["entities"] if e["name"] == f"Dr. Meier {sfx}")
    assert shared["is_owner"] is False
    assert shared["share_status"] == "shared"
    assert shared["mention_count"] is None and shared["last_mentioned_at"] is None
    assert f"Secret Project {sfx}" not in bob.text

    alice = _as(kg_app, monkeypatch, world["alice"]).get(
        "/api/v1/kg/entities", params={"limit": 200}
    )
    assert _names(alice, sfx) == [f"Dr. Meier {sfx}", f"Secret Project {sfx}"]  # once, not twice
    own = next(e for e in alice.json()["entities"] if e["name"] == f"Dr. Meier {sfx}")
    assert own["share_status"] == "shared" and own["is_owner"] is True
    assert isinstance(own["mention_count"], int)


def test_kind_mapping_live(world, kg_app, monkeypatch):
    ms, sfx = world["ms"], world["sfx"]
    me = world["nosharing"]  # allows_shared=false: only own personal rows count
    for name, etype in [
        ("A", "PERSON"),
        ("B", "person"),
        ("C", "ORG"),
        ("D", "PROJECT"),
        ("E", "CONCEPT"),
        ("F", "FILE"),
        ("G", "function"),
    ]:
        _ent(ms, f"{name} {sfx}", me, etype=etype)
    _ent(ms, f"Staged {sfx}", me, etype="PERSON", is_staged=True)  # hidden

    client = _as(kg_app, monkeypatch, me)
    resp = client.get("/api/v1/kg/entities", params={"limit": 200})
    assert resp.json()["counts"] == {"person": 2, "org": 1, "project": 1, "concept": 1, "other": 2}
    assert resp.json()["total"] == 7
    one = client.get("/api/v1/kg/entities", params={"kind": "person"})
    assert one.json()["total"] == 2
    assert {e["kind"] for e in one.json()["entities"]} == {"person"}
    assert f"Staged {sfx}" not in resp.text


def test_list_sort_and_paging(world, kg_app, monkeypatch):
    ms, sfx = world["ms"], world["sfx"]
    me = world["nosharing"]
    busy = _ent(ms, f"Busy {sfx}", me)
    _ent(ms, f"Alpha {sfx}", me)
    _relate(ms, busy, me, "MEMORY", "mcp:add_entity:1")
    _relate(ms, busy, me, "MEMORY", "mcp:add_entity:2")
    client = _as(kg_app, monkeypatch, me)
    by_mentions = client.get("/api/v1/kg/entities", params={"sort": "mentions"}).json()
    assert [e["name"] for e in by_mentions["entities"]] == [f"Busy {sfx}", f"Alpha {sfx}"]
    assert by_mentions["entities"][0]["mention_count"] == 2
    by_name = client.get("/api/v1/kg/entities", params={"sort": "name"}).json()
    assert [e["name"] for e in by_name["entities"]] == [f"Alpha {sfx}", f"Busy {sfx}"]
    recent = client.get("/api/v1/kg/entities", params={"sort": "recent"}).json()
    assert recent["entities"][0]["name"] == f"Busy {sfx}"
    page = client.get("/api/v1/kg/entities", params={"limit": 1, "offset": 1, "sort": "name"})
    assert [e["name"] for e in page.json()["entities"]] == [f"Busy {sfx}"]
    assert page.json()["total"] == 2


# ── mentions ─────────────────────────────────────────────────────────────────


def test_mention_count_counts_existing_items(world, kg_app, monkeypatch):
    ms, sfx, alice = world["ms"], world["sfx"], world["alice"]
    meier = _ent(ms, f"Dr. Meier {sfx}", alice, mention_count=7)  # the column lies
    d1 = f"/data/{sfx}/d1.md"
    _doc(ms, alice, d1)
    _relate(ms, meier, alice, "DOCUMENT", d1)  # exists
    _relate(ms, meier, alice, "DOCUMENT", f"/data/{sfx}/gone.md")  # no file_index row
    s1 = _session(ms, alice, "Talked to Dr. Meier")
    _relate(ms, meier, alice, "SESSION", s1)  # exists
    _relate(ms, meier, alice, "SESSION", str(uuid.uuid4()))  # purged: no sessions row
    _relate(ms, meier, alice, "MEMORY", "mcp:add_entity:abc")

    client = _as(kg_app, monkeypatch, alice)
    listed = client.get("/api/v1/kg/entities", params={"limit": 200}).json()["entities"]
    item = next(e for e in listed if e["name"] == f"Dr. Meier {sfx}")
    assert item["mention_count"] == 3
    mentions = client.get(f"/api/v1/kg/entities/{meier}/mentions").json()
    assert mentions["total"] == 3  # the list and the detail page agree
    assert mentions["counts"] == {"document": 1, "chat": 1, "ai_tool": 1}


def test_mentions_viewer_filter(world, kg_app, monkeypatch):
    ms, sfx, alice, bob = world["ms"], world["sfx"], world["alice"], world["bob"]
    meier = _ent(ms, f"Dr. Meier {sfx}", alice)
    proj = _ent(ms, f"Dr. Meier {sfx}", alice, scope="shared", published_from=meier)
    d1_path, d2_path = f"/data/{sfx}/d1.md", f"/data/{sfx}/d2.md"
    d1 = _doc(ms, alice, d1_path)
    d1_shared = _doc(ms, alice, d1_path, scope="shared", published_from=d1)
    d2 = _doc(ms, alice, d2_path)
    s1 = _session(ms, alice, "Private chat about Dr. Meier")
    _relate(ms, meier, alice, "DOCUMENT", d1_path)
    _relate(ms, meier, alice, "DOCUMENT", d2_path)
    _relate(ms, meier, alice, "SESSION", s1)
    _relate(ms, meier, alice, "MEMORY", "mcp:add_entity:abc")

    a = _as(kg_app, monkeypatch, alice).get(f"/api/v1/kg/entities/{meier}/mentions").json()
    assert a["total"] == 4
    assert {m["ref_id"] for m in a["mentions"] if m["kind"] == "document"} == {str(d1), str(d2)}
    assert {m["kind"] for m in a["mentions"]} == {"document", "chat", "ai_tool"}

    b = _as(kg_app, monkeypatch, bob).get(f"/api/v1/kg/entities/{proj}/mentions").json()
    assert b["total"] == 1
    assert [m["ref_id"] for m in b["mentions"]] == [str(d1_shared)]  # the shared copy
    assert b["counts"] == {"document": 1, "chat": 0, "ai_tool": 0}
    # Bob can't reach Alice's personal entry at all.
    assert (
        _as(kg_app, monkeypatch, bob).get(f"/api/v1/kg/entities/{meier}/mentions").status_code
        == 404
    )


def test_allows_shared_false_member(world, kg_app, monkeypatch):
    ms, sfx = world["ms"], world["sfx"]
    meier = _ent(ms, f"Dr. Meier {sfx}", world["alice"])
    proj = _ent(ms, f"Dr. Meier {sfx}", world["alice"], scope="shared", published_from=meier)
    client = _as(kg_app, monkeypatch, world["nosharing"])
    listed = client.get("/api/v1/kg/entities", params={"limit": 200})
    assert _names(listed, sfx) == []
    assert client.get(f"/api/v1/kg/entities/{proj}/mentions").status_code == 404


def test_allows_shared_false_owner_sees_own_share_status(world, kg_app, monkeypatch):
    ms, sfx, me = world["ms"], world["sfx"], world["nosharing"]
    src = _ent(ms, f"Mine {sfx}", me)
    _ent(ms, f"Mine {sfx}", me, scope="shared", published_from=src)
    client = _as(kg_app, monkeypatch, me)
    allr = client.get("/api/v1/kg/entities", params={"limit": 200}).json()["entities"]
    mine = [e for e in allr if e["name"] == f"Mine {sfx}"]
    assert len(mine) == 1 and mine[0]["share_status"] == "shared"
    only = client.get("/api/v1/kg/entities", params={"shared": "only", "limit": 200}).json()
    assert [e["name"] for e in only["entities"]] == [f"Mine {sfx}"]
    excl = client.get("/api/v1/kg/entities", params={"shared": "exclude", "limit": 200}).json()
    assert excl["entities"] == []


def test_mentions_and_patch_non_uuid_404(world, kg_app, monkeypatch):
    client = _as(kg_app, monkeypatch, world["alice"])
    assert client.get("/api/v1/kg/entities/not-a-uuid/mentions").status_code == 404
    resp = client.patch("/api/v1/kg/entities/not-a-uuid", json={"name": "x", "version": 1})
    assert resp.status_code == 404  # 22P02 must not surface as a 503


# ── rename ───────────────────────────────────────────────────────────────────


def test_bob_patch_alice_personal_404_and_projection_403(world, kg_app, monkeypatch):
    ms, sfx = world["ms"], world["sfx"]
    meier = _ent(ms, f"Dr. Meier {sfx}", world["alice"])
    proj = _ent(ms, f"Dr. Meier {sfx}", world["alice"], scope="shared", published_from=meier)
    bob = _as(kg_app, monkeypatch, world["bob"])
    assert (
        bob.patch(f"/api/v1/kg/entities/{meier}", json={"name": "x", "version": 1}).status_code
        == 404
    )
    resp = bob.patch(f"/api/v1/kg/entities/{proj}", json={"name": "x", "version": 1})
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "not_owner"
    alice = _as(kg_app, monkeypatch, world["alice"])
    resp = alice.patch(f"/api/v1/kg/entities/{proj}", json={"name": "x", "version": 1})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "not_editable"
    assert resp.json()["detail"]["source_entity_id"] == meier
    assert ms.fetch_one("SELECT name FROM entities WHERE entity_id = %s", (meier,))["name"] == (
        f"Dr. Meier {sfx}"
    )


def test_rename_end_to_end(world, kg_app, monkeypatch):
    ms, sfx, alice = world["ms"], world["sfx"], world["alice"]
    old, new = f"Dr. Meier {sfx}", f"Hans Meier {sfx}"
    meier = _ent(ms, old, alice)
    proj = _ent(ms, old, alice, scope="shared", published_from=meier)
    ms.execute("UPDATE entities SET share_origin = 'user' WHERE entity_id = %s", (proj,))
    ms.execute(
        "UPDATE entities SET graph_projected_at = NOW() WHERE entity_id IN (%s, %s)", (meier, proj)
    )
    v = _version(ms, meier)

    resp = _as(kg_app, monkeypatch, alice).patch(
        f"/api/v1/kg/entities/{meier}", json={"name": new, "version": v}
    )
    assert resp.status_code == 200, resp.text
    card = resp.json()
    assert card["name"] == new and old in card["aliases"] and card["version"] == v + 1

    row = ms.fetch_one(
        "SELECT name, aliases, version, graph_projected_at FROM entities WHERE entity_id = %s",
        (meier,),
    )
    assert row["name"] == new and old in row["aliases"] and row["graph_projected_at"] is None
    proj_row = ms.fetch_one(
        "SELECT name, share_origin, graph_projected_at FROM entities WHERE entity_id = %s", (proj,)
    )
    assert proj_row["name"] == new
    assert proj_row["share_origin"] == "user"
    assert proj_row["graph_projected_at"] is None
    bob = _as(kg_app, monkeypatch, world["bob"]).get("/api/v1/kg/entities", params={"limit": 200})
    assert _names(bob, sfx) == [new]

    # A stale dialog: same version again is a conflict, with the current one.
    stale = _as(kg_app, monkeypatch, alice).patch(
        f"/api/v1/kg/entities/{meier}", json={"name": "Other", "version": v}
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == v + 1


def test_rename_delta_survives_extraction_merge(world, kg_app, monkeypatch):
    from services.entities import store_entities

    ms, sfx, alice = world["ms"], world["sfx"], world["alice"]
    old = f"Dr. Meier {sfx}"
    meier = _ent(ms, old, alice, aliases=["Meier"], tags=["clinic", "hospital"])
    monkeypatch.setattr("services.entities.hooks.fire_background", lambda *a, **k: None)
    v = _version(ms, meier)  # the dialog opens here

    store_entities(  # extraction merges "Hans" in; version does not move
        [
            ExtractedEntity(
                name=old,
                entity_type="PERSON",
                aliases=["Hans"],
                context_tags=["clinic", "hospital"],
            )
        ],
        evidence_id=f"/data/{sfx}/x.md",
        evidence_type="DOCUMENT",
        user_id=alice,
        skip_quality_gate=True,
    )
    assert _version(ms, meier) == v

    resp = _as(kg_app, monkeypatch, alice).patch(
        f"/api/v1/kg/entities/{meier}",
        json={"name": f"Hans Meier {sfx}", "aliases_add": ["HM"], "version": v},
    )
    assert resp.status_code == 200, resp.text
    assert {"Hans", "HM", "Meier", old} <= set(resp.json()["aliases"])


def test_rename_then_reingest_keeps_entity_id(world, kg_app, monkeypatch):
    from services.entities import store_entities

    ms, sfx, alice = world["ms"], world["sfx"], world["alice"]
    old, new = f"Dr. Meier {sfx}", f"Hans Meier {sfx}"
    meier = _ent(ms, old, alice, tags=["clinic", "hospital"])
    resp = _as(kg_app, monkeypatch, alice).patch(
        f"/api/v1/kg/entities/{meier}", json={"name": new, "version": _version(ms, meier)}
    )
    assert resp.status_code == 200
    fired = []
    monkeypatch.setattr("services.entities.hooks.fire_background", lambda *a, **k: fired.append(k))

    ids = store_entities(
        [ExtractedEntity(name=old, entity_type="PERSON", context_tags=["clinic", "hospital"])],
        evidence_id=f"/data/{sfx}/new.pdf",
        evidence_type="DOCUMENT",
        user_id=alice,
        skip_quality_gate=True,
    )
    assert ids == [meier]
    count = ms.fetch_one(
        "SELECT count(*)::int AS n FROM entities WHERE user_id = %s AND scope = 'personal'",
        (alice,),
    )
    assert count["n"] == 1
    assert fired[-1]["name"] == new  # the graph node is not renamed back to the old name


def test_reingest_without_tag_overlap_creates_new_entry(world, kg_app, monkeypatch):
    """The old name is an alias, not an identity guarantee: extraction merges
    only with >= 2 overlapping context tags (documented in the member guide)."""
    from services.entities import store_entities

    ms, sfx, alice = world["ms"], world["sfx"], world["alice"]
    old, new = f"Dr. Meier {sfx}", f"Hans Meier {sfx}"
    meier = _ent(ms, old, alice, tags=["clinic", "hospital"])
    _as(kg_app, monkeypatch, alice).patch(
        f"/api/v1/kg/entities/{meier}", json={"name": new, "version": _version(ms, meier)}
    )
    monkeypatch.setattr("services.entities.hooks.fire_background", lambda *a, **k: None)
    ids = store_entities(
        [ExtractedEntity(name=old, entity_type="PERSON", context_tags=["gardening"])],
        evidence_id=f"/data/{sfx}/new.pdf",
        evidence_type="DOCUMENT",
        user_id=alice,
        skip_quality_gate=True,
    )
    assert ids and ids[0] != meier


def test_rename_collision_409(world, kg_app, monkeypatch):
    ms, sfx, alice = world["ms"], world["sfx"], world["alice"]
    a = _ent(ms, f"Alpha {sfx}", alice)
    b = _ent(ms, f"Beta {sfx}", alice, aliases=[f"Bee {sfx}"])
    stg = _ent(ms, f"Staged {sfx}", alice, is_staged=True)
    client = _as(kg_app, monkeypatch, alice)
    for target, expect_id, staged in [
        (f"beta {sfx}", b, False),  # case-insensitive, against a name
        (f"Bee {sfx}", b, False),  # against an alias
        (f"Staged {sfx}", stg, True),
    ]:
        resp = client.patch(
            f"/api/v1/kg/entities/{a}", json={"name": target, "version": _version(ms, a)}
        )
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "name_in_use"
        assert detail["conflicting_entity_id"] == expect_id
        assert detail["conflicting_is_staged"] is staged
    assert ms.fetch_one("SELECT name FROM entities WHERE entity_id = %s", (a,))["name"] == (
        f"Alpha {sfx}"
    )
    # Another member's entry never collides.
    _ent(ms, f"Gamma {sfx}", world["bob"])
    ok = client.patch(
        f"/api/v1/kg/entities/{a}", json={"name": f"Gamma {sfx}", "version": _version(ms, a)}
    )
    assert ok.status_code == 200


# ── concurrency ──────────────────────────────────────────────────────────────


def test_concurrent_renames_one_wins(world, kg_app, monkeypatch):
    ms, sfx, alice = world["ms"], world["sfx"], world["alice"]
    e1, e2 = _ent(ms, f"One {sfx}", alice), _ent(ms, f"Two {sfx}", alice)
    assert _race(kg_app, ms, monkeypatch, alice, e1, e2, f"Target {sfx}") == (200, 409)


def test_concurrent_renames_negative_control_without_lock(world, kg_app, monkeypatch):
    """Same race with the advisory lock removed: both succeed — so the lock is
    what serialises them, and the test above is not passing by accident."""
    ms, sfx, alice = world["ms"], world["sfx"], world["alice"]
    e1, e2 = _ent(ms, f"One {sfx}", alice), _ent(ms, f"Two {sfx}", alice)
    monkeypatch.setattr(entity_edit, "_LOCK_SQL", "SELECT %s::integer, %s")
    assert _race(kg_app, ms, monkeypatch, alice, e1, e2, f"Target {sfx}") == (200, 200)
