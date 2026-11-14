# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Ingest-orchestration tests for code-structure ingest (LUM-301).

Most use an in-memory ``FakeGraphStore`` with MERGE semantics to exercise the
walk→parse→map→project→sweep pipeline without FalkorDB. The idempotency proof
that must catch a CREATE-vs-MERGE regression is a separate DB-backed test,
skipped automatically when FalkorDB is unavailable (runs in CI).
"""

import re
import threading

import pytest
from codegraph.ingest import ingest_roots


class FakeGraphStore:
    """Minimal MERGE-semantics double for the code-structure projector.

    Nodes MERGE on (lumogis_id, user_id); edges MERGE on (from,to,rel,evidence_id);
    sweep DETACH-DELETEs file-scoped nodes from older runs.
    """

    def __init__(self, fail_on_edge: str | None = None):
        self.nodes: dict[tuple[str, str], dict] = {}
        self.edges: set[tuple[str, str, str, str]] = set()
        self._fail_on_edge = fail_on_edge

    def create_node(self, labels, properties):
        key = (properties["lumogis_id"], properties["user_id"])
        self.nodes[key] = dict(properties)
        return f"n:{properties['lumogis_id']}"

    def query(self, cypher, params=None):
        params = params or {}
        if "DETACH DELETE" in cypher:
            doomed = [
                k
                for k, p in self.nodes.items()
                if p.get("user_id") == params.get("uid")
                and p.get("evidence_type") == params.get("etype")
                and p.get("file") == params.get("file")
                and p.get("ingest_run_id") != params.get("run")
            ]
            for k in doomed:
                self.nodes.pop(k, None)
            return []
        if "MERGE (a)" in cypher:
            rel = re.search(r"-\[r:(\w+)", cypher).group(1)
            if self._fail_on_edge and rel == self._fail_on_edge:
                raise RuntimeError("simulated FalkorDB failure")
            a = (params["from_id"], params["uid"])
            b = (params["to_id"], params["uid"])
            if a in self.nodes and b in self.nodes:
                self.edges.add((params["from_id"], params["to_id"], rel, params["eid"]))
                return [{"ok": 1}]
            return []
        return []


_REPO_A_PY = b"import requests\n\n\ndef a():\n    return b()\n\n\ndef b():\n    return 1\n"


def _write_repo(tmp_path):
    (tmp_path / "a.py").write_bytes(_REPO_A_PY)
    return str(tmp_path)


def test_ingest_roots_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMOGIS_CODEGRAPH_LANGS", "python,typescript")
    gs = FakeGraphStore()
    root = _write_repo(tmp_path)
    report = ingest_roots([root], "coding", gs=gs)
    assert report.files_parsed == 1
    assert report.components > 0
    assert report.edges > 0  # at least the a->b CALLS edge
    # a CALLS edge and a DEPENDS_ON-to-Library edge were projected
    rels = {e[2] for e in gs.edges}
    assert "CALLS" in rels
    assert "DEPENDS_ON" in rels


def test_ingest_skips_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMOGIS_CODEGRAPH_MAX_FILE_KB", "1")
    big = tmp_path / "big.py"
    big.write_bytes(b"x = 1\n" + b"# pad\n" * 5000)  # > 1 KB
    gs = FakeGraphStore()
    report = ingest_roots([str(tmp_path)], "coding", gs=gs)
    assert any(p.endswith("big.py") for p in report.oversize_skipped)
    assert report.files_parsed == 0


def test_ingest_counts_unresolved_calls(tmp_path):
    (tmp_path / "u.py").write_bytes(b"def a():\n    return missing()\n")
    gs = FakeGraphStore()
    report = ingest_roots([str(tmp_path)], "coding", gs=gs)
    assert report.unresolved_calls == 1


def test_partial_failure_leaves_prior_intact(tmp_path):
    # First successful run populates the store.
    (tmp_path / "a.py").write_bytes(b"def a():\n    return b()\n\n\ndef b():\n    return 1\n")
    gs = FakeGraphStore()
    first = ingest_roots([str(tmp_path)], "coding", gs=gs)
    nodes_before = dict(gs.nodes)
    assert first.components > 0

    # Second run fails while writing CALLS edges (after nodes were MERGEd).
    gs._fail_on_edge = "CALLS"
    with pytest.raises(RuntimeError):
        ingest_roots([str(tmp_path)], "coding", gs=gs)
    # Nodes from the prior complete generation are still present (no half-delete:
    # the sweep never ran because projection raised first).
    assert nodes_before.keys() <= gs.nodes.keys()


def test_report_counts_match_actual_nodes_edges(tmp_path):
    # P2-1/P2-2: report.components/edges must equal the real (deduped) counts.
    # x.py has a duplicate call site (a->b twice) and both files import requests.
    (tmp_path / "x.py").write_bytes(
        b"import requests\n\n\ndef a():\n    b()\n    return b()\n\n\ndef b():\n    return 1\n"
    )
    (tmp_path / "y.py").write_bytes(b"import requests\n")
    gs = FakeGraphStore()
    report = ingest_roots([str(tmp_path)], "coding", gs=gs)
    # One distinct CALLS edge despite two call sites; one Library despite two files.
    assert report.components == len(gs.nodes)
    assert report.edges == len(gs.edges)
    assert len([e for e in gs.edges if e[2] == "CALLS"]) == 1


def test_concurrent_ingest_is_serialized(tmp_path, monkeypatch):
    """Second reindex waits for the first project+sweep to finish (no cross-run sweep)."""
    from codegraph import ingest as ingest_mod
    from graph import writer as writer_mod

    held = threading.Event()
    proceed = threading.Event()
    orig_project = writer_mod.project_code_structure

    def holding_project(*args, **kwargs):
        held.set()
        proceed.wait(timeout=5)
        return orig_project(*args, **kwargs)

    monkeypatch.setattr(writer_mod, "project_code_structure", holding_project)

    gs = FakeGraphStore()
    (tmp_path / "a.py").write_bytes(b"def a():\n    return 1\n")
    root = str(tmp_path)

    t1 = threading.Thread(target=lambda: ingest_roots([root], "coding", gs=gs))
    t1.start()
    held.wait(timeout=5)
    assert not ingest_mod._INGEST_LOCK.acquire(blocking=False)
    proceed.set()
    t1.join(timeout=10)

    ingest_roots([root], "coding", gs=gs)
    run_ids = {p.get("ingest_run_id") for p in gs.nodes.values()}
    assert len(run_ids) == 1
    assert gs.nodes


@pytest.mark.skip(reason="LUM-536: deleted/renamed-file sweep not implemented in v1")
def test_deleted_file_nodes_swept(tmp_path):
    # Index a file, delete it, re-index → its nodes should be gone. Currently the
    # per-file sweep only touches files seen on the current run, so deleted-file
    # nodes leak (tracked as LUM-536). This test will pass once run-granular sweep lands.
    f = tmp_path / "gone.py"
    f.write_bytes(b"def a():\n    return 1\n")
    gs = FakeGraphStore()
    ingest_roots([str(tmp_path)], "coding", gs=gs)
    f.unlink()
    ingest_roots([str(tmp_path)], "coding", gs=gs)
    assert not [p for p in gs.nodes.values() if p.get("file") == "gone.py"]


def _falkordb_or_skip():
    try:
        import config

        gs = config.get_graph_store()
        # Require a *real* store: the service conftest injects a MockGraphStore
        # (no create_node) for unit runs — that must not satisfy a DB-backed test.
        if gs is None or not hasattr(gs, "create_node") or not gs.ping():
            pytest.skip("real FalkorDB unavailable")
        return gs
    except Exception:
        pytest.skip("real FalkorDB unavailable")


@pytest.mark.integration
def test_reindex_is_idempotent_dbbacked(tmp_path):
    """DB-backed idempotency: re-index twice → identical node/edge counts.

    Mirrors arbitration R1 P1-6 — a mock with MERGE baked in cannot catch a real
    CREATE-vs-MERGE regression, so this runs against a live FalkorDB (skipped
    when none is reachable, e.g. local unit-only runs)."""
    gs = _falkordb_or_skip()
    (tmp_path / "a.py").write_bytes(b"def a():\n    return b()\n\n\ndef b():\n    return 1\n")
    r1 = ingest_roots([str(tmp_path)], "coding", gs=gs)
    r2 = ingest_roots([str(tmp_path)], "coding", gs=gs)
    assert r1.components == r2.components
    assert r1.edges == r2.edges
