# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""PoC timings for large-document entity cascade batching (LUM-605).

Measures per-entity ``project_entity`` (embed + Postgres + Qdrant) and
``project_entity_into_graph`` (FalkorDB MERGE + edge sweep) at representative
entity counts. Writes ``lum605-cascade-poc-report.json`` beside this file when
``LUM605_POC=1``.

Run (live stack):
  LUM605_POC=1 PYTHONPATH=/project/services/lumogis-graph:. \\
    pytest tests/premium/test_lum605_entity_cascade_batching_poc.py -v -s
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pytest
from auth import UserContext

import config
from services import projection

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPORT_PATH = Path(__file__).resolve().parent / "lum605-cascade-poc-report.json"
SIZES = (10, 50, 100, 200)
# Realistic upper bound for entity extraction on a very large household doc.
STRESS_SIZE = 300


def _poc_enabled() -> bool:
    return os.environ.get("LUM605_POC", "").strip() in ("1", "true", "yes")


@pytest.fixture
def live_stack(monkeypatch):
    if not _poc_enabled():
        pytest.skip("set LUM605_POC=1 to run the LUM-605 PoC benchmark")

    url = os.environ.get("FALKORDB_URL", "redis://falkordb:6379")
    if not url:
        pytest.skip("FALKORDB_URL not set")

    monkeypatch.setenv("GRAPH_BACKEND", "falkordb")
    monkeypatch.setenv("FALKORDB_URL", url)
    monkeypatch.setattr(config, "get_graph_mode", lambda: "service")

    for key in list(config._instances.keys()):
        if key.startswith("graph_store:") or key == "graph_store":
            config._instances.pop(key, None)

    from adapters.postgres_store import PostgresStore
    from adapters.qdrant_store import QdrantStore

    vs = QdrantStore(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
    ms = PostgresStore(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "lumogis"),
        password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        dbname=os.environ.get("POSTGRES_DB", "lumogis"),
    )
    if not ms.ping():
        pytest.skip("postgres unreachable")

    try:
        vs.count("entities")
    except Exception:
        vs.create_collection("entities", int(config.get_embedder().vector_size))

    gs = config.get_graph_store("personal")
    if gs is None or not gs.ping():
        pytest.skip("falkordb unreachable")

    from adapters.ollama_embedder import OllamaEmbedder

    ollama_url = os.environ.get("OLLAMA_URL", "http://ollama:11434")
    embed_model = os.environ.get("EMBED_MODEL", "nomic-embed-text")
    embedder = OllamaEmbedder(ollama_url, embed_model)
    if not embedder.ping():
        pytest.skip("ollama embedder unavailable")

    monkeypatch.setitem(config._instances, "metadata_store", ms)
    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setitem(config._instances, "embedder", embedder)

    owner = f"lum605-{uuid.uuid4().hex[:8]}"
    actor = UserContext(user_id=owner, is_authenticated=True)
    yield ms, vs, gs, owner, actor
    _cleanup(ms, vs, gs, owner)


def _cleanup(ms, vs, gs, owner: str) -> None:
    for stmt, args in (
        ("DELETE FROM entity_relations WHERE user_id = %s", (owner,)),
        ("DELETE FROM entities WHERE user_id = %s", (owner,)),
    ):
        try:
            ms.execute(stmt, args)
        except Exception:
            pass
    try:
        vs.delete_where("entities", {"must": [{"key": "user_id", "match": {"value": owner}}]})
    except Exception:
        pass
    try:
        gs.query("MATCH (n {user_id: $uid}) DETACH DELETE n", {"uid": owner})
    except Exception:
        pass


def _seed_personal_entities(ms, owner: str, n: int) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        row = ms.fetch_one(
            "INSERT INTO entities (name, entity_type, user_id, scope) "
            "VALUES (%s, 'PERSON', %s, 'personal') RETURNING *",
            (f"Person-{i:04d}", owner),
        )
        rows.append(dict(row))
    return rows


def _seed_personal_graph_chain(gs, owner: str, entity_ids: list[str]) -> None:
    """Linear RELATES_TO chain so each graph projection runs a non-trivial sweep."""
    if not entity_ids:
        return
    gs.query(
        "UNWIND $ids AS eid MERGE (n:Person {lumogis_id: eid, user_id: $u}) "
        "SET n.scope = 'personal'",  # LUM-961: the sweep matches scope = 'personal' only
        {"ids": entity_ids, "u": owner},
    )
    for a, b in zip(entity_ids, entity_ids[1:], strict=False):
        gs.query(
            "MATCH (a:Person {lumogis_id: $a, user_id: $u}) "
            "MATCH (b:Person {lumogis_id: $b, user_id: $u}) "
            "MERGE (a)-[r:RELATES_TO {user_id: $u}]->(b) "
            "SET r.co_occurrence_count = 1, r.evidence_id = $ev",
            {"a": a, "b": b, "u": owner, "ev": f"poc-{a[:8]}"},
        )


def _time_project_entity(actor, rows: list[dict]) -> float:
    t0 = time.perf_counter()
    for row in rows:
        projection.project_entity(row, target_scope="shared", actor=actor, share_origin="document")
    return time.perf_counter() - t0


def _time_embed_sequential(texts: list[str]) -> float:
    embedder = config.get_embedder()
    t0 = time.perf_counter()
    for text in texts:
        embedder.embed(text)
    return time.perf_counter() - t0


def _time_embed_batch(texts: list[str]) -> float:
    embedder = config.get_embedder()
    t0 = time.perf_counter()
    embedder.embed_batch(texts)
    return time.perf_counter() - t0


def _time_graph_projection(owner: str, rows: list[dict]) -> float:
    import projection as kg_projection

    t0 = time.perf_counter()
    for row in rows:
        src_id = str(row["entity_id"])
        proj_id = projection.projection_pk("entities", src_id, "shared")
        kg_projection.project_entity_into_graph(
            src_entity_id=src_id,
            proj_entity_id=proj_id,
            target_scope="shared",
            actor_user_id=owner,
            name=str(row.get("name") or ""),
            entity_type=str(row.get("entity_type") or "PERSON"),
        )
    return time.perf_counter() - t0


def test_lum605_entity_cascade_batching_poc(live_stack):
    ms, vs, gs, owner, actor = live_stack
    report: dict = {
        "issue": "LUM-605",
        "sizes": list(SIZES) + [STRESS_SIZE],
        "environment": {
            "postgres_host": os.environ.get("POSTGRES_HOST", "postgres"),
            "qdrant_url": os.environ.get("QDRANT_URL", "http://qdrant:6333"),
            "falkordb_url": os.environ.get("FALKORDB_URL", "redis://falkordb:6379"),
            "embedder": type(config.get_embedder()).__name__,
            "embed_model": os.environ.get("EMBED_MODEL", "nomic-embed-text"),
        },
        "runs": [],
    }

    for n in (*SIZES, STRESS_SIZE):
        rows = _seed_personal_entities(ms, owner, n)
        texts = [f"{r['name']} PERSON" for r in rows]
        entity_ids = [str(r["entity_id"]) for r in rows]
        _seed_personal_graph_chain(gs, owner, entity_ids)

        embed_seq_s = _time_embed_sequential(texts)
        embed_batch_s = _time_embed_batch(texts)
        project_entity_s = _time_project_entity(actor, rows)
        graph_s = _time_graph_projection(owner, rows)

        per_entity = {
            "embed_sequential_ms": round(1000 * embed_seq_s / n, 2),
            "embed_batch_ms": round(1000 * embed_batch_s / n, 2),
            "project_entity_ms": round(1000 * project_entity_s / n, 2),
            "graph_merge_sweep_ms": round(1000 * graph_s / n, 2),
        }
        run = {
            "entity_count": n,
            "embed_sequential_s": round(embed_seq_s, 3),
            "embed_batch_s": round(embed_batch_s, 3),
            "project_entity_total_s": round(project_entity_s, 3),
            "graph_projection_total_s": round(graph_s, 3),
            "cascade_total_s": round(project_entity_s + graph_s, 3),
            "per_entity_ms": per_entity,
            "embed_batch_speedup_vs_sequential": round(embed_seq_s / embed_batch_s, 2)
            if embed_batch_s > 0
            else None,
        }
        report["runs"].append(run)
        print(json.dumps(run, indent=2))

        _cleanup(ms, vs, gs, owner)

    # Decision helpers (background share job SLO: target <60s for 200 entities).
    run200 = next(r for r in report["runs"] if r["entity_count"] == 200)
    report["decision"] = {
        "slo_background_share_200_entities_s": 60,
        "cascade_200_entities_s": run200["cascade_total_s"],
        "dominant_cost": "embed+pg+qdrant (project_entity)"
        if run200["project_entity_total_s"] > run200["graph_projection_total_s"]
        else "graph_merge_sweep",
        "recommendation": "",
        "batch_threshold_entities": None,
        "follow_up": None,
    }
    dec = report["decision"]
    if run200["cascade_total_s"] <= dec["slo_background_share_200_entities_s"]:
        dec["recommendation"] = "defer_batching"
        dec["batch_threshold_entities"] = None
        dec["follow_up"] = (
            "No implementation child: 200-entity share completes within 60s background SLO. "
            "Revisit if extraction caps rise or SLO tightens."
        )
    elif run200["project_entity_total_s"] > run200["graph_projection_total_s"] * 2:
        dec["recommendation"] = "batch_embeddings_in_cascade"
        dec["batch_threshold_entities"] = 100
        dec["follow_up"] = (
            "Create implementation child: batch ``embed_batch`` in document_entity_cascade "
            "before Qdrant upserts when len(entities) >= threshold."
        )
    else:
        dec["recommendation"] = "batch_graph_unwind"
        dec["batch_threshold_entities"] = 100
        dec["follow_up"] = (
            "Create implementation child: UNWIND batch MERGE in on_document_shared when "
            "len(entities) >= threshold."
        )

    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {REPORT_PATH}")
