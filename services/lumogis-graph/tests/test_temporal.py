# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for graph/temporal.py — LUM-104 temporal mechanics.

Covers the tz/format helpers (the #893 pattern), edge stamping (min-rule),
invalidation writes (MATCH-never-MERGE, the #1001 pattern), SUPERSEDES
recording, NULL-as-absence semantics, and the naive-datetime grep gate.

All graph interaction goes through a recording fake store (same pattern as
orchestrator/tests/premium/test_graph_writer.py) — no FalkorDB needed.
"""

from __future__ import annotations

import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest
from graph import temporal
from graph.schema import TEMPORAL_EDGE_TYPES
from graph.schema import EdgeType

# ---------------------------------------------------------------------------
# Recording fake graph store
# ---------------------------------------------------------------------------


class FakeGraphStore:
    """Recording GraphStore fake with canned per-prefix query results."""

    def __init__(self):
        self.queries: list[tuple[str, dict]] = []
        self.nodes: dict[tuple[str, str], dict] = {}
        self.edges: list[dict] = []
        self._canned: list[tuple[str, list[dict]]] = []
        self._next_id = 0

    # -- canned reads -------------------------------------------------------
    def seed(self, cypher_substring: str, rows: list[dict]) -> None:
        self._canned.append((cypher_substring, rows))

    # -- GraphStore protocol -------------------------------------------------
    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        self.queries.append((cypher, params or {}))
        for substring, rows in self._canned:
            if substring in cypher:
                return list(rows)
        return []

    def create_node(self, labels: list[str], properties: dict) -> str:
        key = (str(properties.get("lumogis_id")), str(properties.get("user_id")))
        if key in self.nodes:
            self.nodes[key]["props"].update(properties)
            return self.nodes[key]["id"]
        self._next_id += 1
        nid = str(self._next_id)
        self.nodes[key] = {"id": nid, "labels": list(labels), "props": dict(properties)}
        return nid

    def create_edge(self, from_id: str, to_id: str, rel_type: str, properties: dict) -> None:
        evidence_id = properties.get("evidence_id", "")
        for edge in self.edges:
            if (
                edge["from_id"] == from_id
                and edge["to_id"] == to_id
                and edge["rel_type"] == rel_type
                and edge["props"].get("evidence_id", "") == evidence_id
            ):
                edge["props"].update(properties)
                return
        self.edges.append(
            {"from_id": from_id, "to_id": to_id, "rel_type": rel_type, "props": dict(properties)}
        )


@pytest.fixture
def gs():
    return FakeGraphStore()


# ---------------------------------------------------------------------------
# tz/format helpers — the #893 pattern pinned
# ---------------------------------------------------------------------------


def test_ensure_utc_naive_assumes_utc():
    naive = datetime(2026, 7, 3, 12, 0, 0)
    out = temporal.ensure_utc(naive)
    assert out.tzinfo is not None
    assert out.utcoffset() == timedelta(0)
    assert out.hour == 12  # naive treated AS UTC, not converted


def test_ensure_utc_aware_converts():
    plus2 = timezone(timedelta(hours=2))
    aware = datetime(2026, 7, 3, 14, 0, 0, tzinfo=plus2)
    out = temporal.ensure_utc(aware)
    assert out.utcoffset() == timedelta(0)
    assert out.hour == 12  # converted, not reinterpreted


def test_graph_ts_round_trip_fixed_format():
    dt = datetime(2026, 7, 3, 8, 30, 15, tzinfo=timezone.utc)
    s = temporal.to_graph_ts(dt)
    assert s == "2026-07-03T08:30:15Z"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", s)
    back = temporal.parse_graph_ts(s)
    assert back == dt


def test_to_graph_ts_truncates_microseconds_and_offsets():
    plus2 = timezone(timedelta(hours=2))
    dt = datetime(2026, 7, 3, 14, 0, 0, 123456, tzinfo=plus2)
    assert temporal.to_graph_ts(dt) == "2026-07-03T12:00:00Z"


def test_parse_accepts_z_and_offset():
    a = temporal.parse_graph_ts("2026-07-03T08:30:15Z")
    b = temporal.parse_graph_ts("2026-07-03T08:30:15+00:00")
    assert a == b
    # legacy _now_iso() shape: microseconds + offset
    c = temporal.parse_graph_ts("2026-07-03T08:30:15.123456+00:00")
    assert c.microsecond == 123456
    assert c.utcoffset() == timedelta(0)


def test_parse_graph_ts_naive_string_assumes_utc():
    out = temporal.parse_graph_ts("2026-07-03T08:30:15")
    assert out.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# Feature-flag helper — parse parity with orchestrator features._TRUTHY
# ---------------------------------------------------------------------------

# Shared truthy fixture (mirrored by the Core-side test in
# orchestrator/tests/premium/test_temporal_pipeline.py so the two parsers
# cannot drift: same inputs, same expected outputs).
FLAG_PARSE_CASES = [
    ("on", True),
    ("1", True),
    ("TRUE", True),
    ("true", True),
    ("yes", True),
    (" on ", True),
    ("off", False),
    ("0", False),
    ("", False),
    ("false", False),
    ("banana", False),
]


@pytest.mark.parametrize("raw,expected", FLAG_PARSE_CASES)
def test_ff_parse_identical_core_and_kg_side(monkeypatch, raw, expected):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", raw)
    assert temporal.temporal_feature_enabled() is expected


def test_ff_unset_means_disabled(monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    assert temporal.temporal_feature_enabled() is False


# ---------------------------------------------------------------------------
# Naive-datetime grep gate (mirrors the merge-policy check pattern)
# ---------------------------------------------------------------------------


def test_temporal_code_has_no_naive_datetime_calls():
    """Ban naive clock reads and unwrapped parses in temporal code.

    `datetime.now()` (no tz), `datetime.utcnow()`, and `fromisoformat` are
    prohibited in graph/temporal.py and the Core-side temporal_pipeline.py;
    the single sanctioned `fromisoformat` lives inside parse_graph_ts and is
    marked `# gate-allow: fromisoformat`.
    """
    kg_root = Path(__file__).resolve().parents[1]
    candidates = [kg_root / "graph" / "temporal.py"]
    # Core-side pipeline (repo checkout layout); skipped in the standalone KG
    # Docker image where only /app exists (kg_root.parent is not the repo root).
    orchestrator_pipeline = (
        kg_root.parent / "orchestrator" / "plugins" / "graph" / "temporal_pipeline.py"
    )
    if orchestrator_pipeline.is_file():
        candidates.append(orchestrator_pipeline)
    banned_naive = re.compile(r"datetime\.now\(\)|datetime\.utcnow\(|\.utcnow\(")
    offenders: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if banned_naive.search(line):
                offenders.append(f"{path.name}:{lineno}: naive datetime call")
            if "fromisoformat" in line and "gate-allow: fromisoformat" not in line:
                offenders.append(f"{path.name}:{lineno}: bare fromisoformat")
    assert not offenders, "temporal tz-discipline violations:\n" + "\n".join(offenders)


def test_temporal_check_status_enum_is_closed():
    assert temporal.TEMPORAL_CHECK_STATUSES == (
        "ok",
        "extract_failed",
        "judge_failed",
        "text_unavailable",
        "skipped",
        "backfill",
    )
    gs_ = FakeGraphStore()
    with pytest.raises(ValueError):
        temporal.stamp_temporal_checked(
            gs_, evidence_id="n1", evidence_type="NOTE", user_id="u1", status="bogus"
        )


# ---------------------------------------------------------------------------
# Stamping — min-rule + per-edge app-side edge_uid
# ---------------------------------------------------------------------------


def _utc(y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


def test_stamp_evidence_applies_min_rule_with_fixed_format(gs):
    gs.seed("MATCH ()-[r:MENTIONS]->()", [{"rid": 7, "edge_uid": None}])
    n = temporal.stamp_valid_at_for_evidence(
        gs, evidence_id="note-1", valid_at=_utc(2025, 3, 1, 12), user_id="u1"
    )
    assert n == 1
    set_queries = [(c, p) for c, p in gs.queries if "SET r.valid_at" in c]
    assert len(set_queries) == 1
    cypher, params = set_queries[0]
    # min-rule shape: only ever moves valid_at backward
    assert "CASE WHEN r.valid_at IS NULL OR $v < r.valid_at" in cypher
    assert params["v"] == "2025-03-01T12:00:00Z"  # fixed format, not isoformat()
    assert "created_at = coalesce(r.created_at" in cypher
    assert "contradiction_count = coalesce(r.contradiction_count, 0)" in cypher
    # edge_uid assigned app-side per edge, coalesce-guarded
    assert "r.edge_uid = coalesce(r.edge_uid, $edge_uid)" in cypher
    assert params["edge_uid"]


def test_stamp_preserves_existing_edge_uid(gs):
    gs.seed("MATCH ()-[r:MENTIONS]->()", [{"rid": 7, "edge_uid": "keep-me"}])
    temporal.stamp_valid_at_for_evidence(
        gs, evidence_id="note-1", valid_at=_utc(2025, 3, 1), user_id="u1"
    )
    _, params = [(c, p) for c, p in gs.queries if "SET r.valid_at" in c][0]
    assert params["edge_uid"] == "keep-me"


def test_stamp_pairs_recanonicalises_direction(gs):
    gs.seed("MATCH (a)-[r:RELATES_TO]->(b)", [{"rid": 3, "edge_uid": "x"}])
    temporal.stamp_valid_at_for_pairs(
        gs, pairs=[("zzz", "aaa")], valid_at=_utc(2025, 1, 1), user_id="u1"
    )
    match_q = [(c, p) for c, p in gs.queries if "RELATES_TO" in c][0]
    assert match_q[1]["a_id"] == "aaa"  # lower→higher per ADR-038
    assert match_q[1]["b_id"] == "zzz"


def test_valid_at_min_rule_never_advances_on_recurrence(gs):
    """The stamp Cypher itself carries the guard: a later $v never overwrites."""
    gs.seed("MATCH ()-[r:MENTIONS]->()", [{"rid": 1, "edge_uid": "e1"}])
    temporal.stamp_valid_at_for_evidence(
        gs, evidence_id="n", valid_at=_utc(2026, 6, 1), user_id="u1"
    )
    cypher, _ = [(c, p) for c, p in gs.queries if "SET r.valid_at" in c][0]
    assert "ELSE r.valid_at END" in cypher  # keeps earlier value when $v >= existing


# ---------------------------------------------------------------------------
# Invalidation — MATCH never MERGE (#1001), absence-as-NULL, soft-only
# ---------------------------------------------------------------------------


def test_apply_invalidation_matches_never_merges(gs):
    gs.seed("WHERE r.edge_uid = $edge_uid", [{"rid": 5}])
    ok = temporal.apply_invalidation(
        gs,
        edge_uid="abc",
        invalid_at=_utc(2026, 5, 1),
        invalidated_by_evidence_id="note-9",
        reason="fact superseded",
        user_id="u1",
    )
    assert ok is True
    cypher, params = gs.queries[-1]
    assert "MATCH" in cypher and "MERGE" not in cypher
    assert "DELETE" not in cypher.upper()
    assert params["invalid_at"] == "2026-05-01T00:00:00Z"
    assert "contradiction_count = coalesce(r.contradiction_count, 0) + 1" in cypher


def test_apply_invalidation_missing_edge_uid_skips(gs):
    ok = temporal.apply_invalidation(
        gs,
        edge_uid="gone",
        invalid_at=_utc(2026, 5, 1),
        invalidated_by_evidence_id="note-9",
        reason="x",
        user_id="u1",
    )
    assert ok is False  # skip-and-log, nothing created


def test_apply_invalidation_truncates_reason(gs):
    gs.seed("WHERE r.edge_uid = $edge_uid", [{"rid": 5}])
    temporal.apply_invalidation(
        gs,
        edge_uid="abc",
        invalid_at=_utc(2026, 5, 1),
        invalidated_by_evidence_id="n",
        reason="R" * 2000,
        user_id="u1",
    )
    _, params = gs.queries[-1]
    assert len(params["reason"]) == 500  # MAX_TEXT_LENGTH


def test_invalid_at_absence_means_currently_valid(gs):
    """No sentinel strings: nothing in this module writes invalid_at except
    apply_invalidation, and no code path emits an empty-string timestamp."""
    gs.seed("MATCH ()-[r:MENTIONS]->()", [{"rid": 1, "edge_uid": "e"}])
    temporal.stamp_valid_at_for_evidence(
        gs, evidence_id="n", valid_at=_utc(2026, 1, 1), user_id="u1"
    )
    for cypher, params in gs.queries:
        assert "invalid_at" not in cypher  # stamping never touches invalid_at
        for v in params.values():
            assert v != ""  # no empty-string sentinel anywhere


# ---------------------------------------------------------------------------
# SUPERSEDES recording
# ---------------------------------------------------------------------------


def test_supersedes_edge_created_with_applied_flag(gs):
    ok = temporal.record_supersedes(
        gs,
        superseding_evidence_id="note-2",
        superseding_evidence_type="NOTE",
        superseded_evidence_id="note-1",
        superseded_evidence_type="NOTE",
        invalidated_edge_uid="uid-1",
        edge_type=EdgeType.RELATES_TO,
        entity_pair=("a", "b"),
        at=_utc(2026, 5, 1),
        applied=True,
        confidence=0.93,
        user_id="u1",
    )
    assert ok is True
    assert len(gs.edges) == 1
    edge = gs.edges[0]
    assert edge["rel_type"] == "SUPERSEDES"
    assert edge["props"]["applied"] is True
    assert edge["props"]["invalidated_edge_uid"] == "uid-1"
    assert edge["props"]["entity_pair"] == "a|b"
    assert edge["props"]["at"] == "2026-05-01T00:00:00Z"
    assert "scope" not in edge["props"]


def test_record_proposal_is_applied_false_and_scope_taggable(gs):
    ok = temporal.record_proposal(
        gs,
        superseding_evidence_id="s2",
        superseding_evidence_type="SESSION",
        superseded_evidence_id="s1",
        superseded_evidence_type="SESSION",
        invalidated_edge_uid="uid-9",
        edge_type=EdgeType.RELATES_TO,
        entity_pair=("x", "y"),
        at=_utc(2026, 6, 1),
        confidence=0.85,
        user_id="u1",
        scope="shared",  # checkpoint fold 2a: shared-scope conflict record
    )
    assert ok is True
    props = gs.edges[0]["props"]
    assert props["applied"] is False
    assert props["scope"] == "shared"


def test_supersedes_retry_is_idempotent(gs):
    for _ in range(2):
        temporal.record_supersedes(
            gs,
            superseding_evidence_id="n2",
            superseding_evidence_type="NOTE",
            superseded_evidence_id="n1",
            superseded_evidence_type="NOTE",
            invalidated_edge_uid="uid-7",
            edge_type=EdgeType.RELATES_TO,
            entity_pair=("a", "b"),
            at=_utc(2026, 5, 1),
            applied=True,
            confidence=0.9,
            user_id="u1",
        )
    assert len(gs.edges) == 1  # MERGE key = invalidated_edge_uid


def test_supersedes_falls_back_when_evidence_type_unknown(gs):
    ok = temporal.record_supersedes(
        gs,
        superseding_evidence_id="n2",
        superseding_evidence_type="MYSTERY",
        superseded_evidence_id="n1",
        superseded_evidence_type="NOTE",
        invalidated_edge_uid="uid-1",
        edge_type=EdgeType.RELATES_TO,
        entity_pair=("a", "b"),
        at=_utc(2026, 5, 1),
        applied=False,
        confidence=0.9,
        user_id="u1",
    )
    assert ok is False
    assert gs.edges == []  # caller falls back to edge-properties-only


# ---------------------------------------------------------------------------
# temporal_checked stamp
# ---------------------------------------------------------------------------


def test_stamp_temporal_checked_sets_status_and_fixed_ts(gs):
    ok = temporal.stamp_temporal_checked(
        gs,
        evidence_id="note-1",
        evidence_type="NOTE",
        user_id="u1",
        status="ok",
        checked_at=_utc(2026, 7, 3, 6),
    )
    assert ok is True
    node = gs.nodes[("note-1", "u1")]
    assert node["props"]["temporal_check_status"] == "ok"
    assert node["props"]["temporal_checked_at"] == "2026-07-03T06:00:00Z"


def test_temporal_cypher_edge_types_from_schema_constants():
    """Allowlist pin: every interpolated relationship type in temporal.py
    comes from schema.py constants (^[A-Z_]+$-safe by construction)."""
    src = (Path(__file__).resolve().parents[1] / "graph" / "temporal.py").read_text(
        encoding="utf-8"
    )
    # every f-string Cypher interpolation of an edge type references EdgeType.*
    for m in re.finditer(r"\[r:\{([A-Za-z_.]+)\}", src):
        assert m.group(1).startswith("EdgeType."), m.group(0)
    for t in TEMPORAL_EDGE_TYPES:
        assert re.fullmatch(r"[A-Z_]+", t)
    assert re.fullmatch(r"[A-Z_]+", EdgeType.SUPERSEDES)


# ---------------------------------------------------------------------------
# Writer stamping under the flag (step 3) — byte-identity + R1
# ---------------------------------------------------------------------------

# The exact RELATES_TO Cypher shipped today — the flag-off byte-identity pin.
_RELATES_TO_CYPHER_TODAY = (
    "MATCH (a) WHERE a.lumogis_id = $a_id AND a.user_id = $uid "
    "MATCH (b) WHERE b.lumogis_id = $b_id AND b.user_id = $uid "
    "MERGE (a)-[r:RELATES_TO]->(b) "
    # LUM-952: absolute distinct-shared-evidence count, monotone (never lowered
    # by a projection), instead of `coalesce(...) + 1`.
    "SET r.co_occurrence_count = CASE "
    "WHEN coalesce(r.co_occurrence_count, 0) < $count THEN $count "
    "ELSE r.co_occurrence_count END, "
    "    r.last_seen_at = $now, "
    "    r.user_id = $uid"
)


def _run_cooccurrence(gs_, mock_metadata_store, monkeypatch, flag: str | None):
    if flag is None:
        monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    else:
        monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", flag)
    import config as kg_config

    # bypass the kg_settings TTL-cache round-trip (it would consume the
    # seeded fetch_all queue before the sibling query runs)
    monkeypatch.setattr(kg_config, "get_graph_max_cooccurrence_pairs", lambda: 100)
    mock_metadata_store._seed_fetch_all([{"sibling_id": "entity-b", "shared": 1}])
    from graph import writer

    writer._update_cooccurrence_edges(gs_, "entity-a", "u1", "ev-1")
    return [(c, p) for c, p in gs_.queries if "RELATES_TO" in c]


def test_relates_to_cypher_identical_when_flag_off(gs, mock_metadata_store, monkeypatch):
    queries = _run_cooccurrence(gs, mock_metadata_store, monkeypatch, None)
    assert len(queries) == 1
    cypher, params = queries[0]
    assert cypher == _RELATES_TO_CYPHER_TODAY  # byte-identical
    assert set(params) == {"a_id", "b_id", "uid", "now", "count"}
    assert params["count"] == 1


def test_relates_to_stamps_valid_at_and_edge_uid_when_flag_on(gs, mock_metadata_store, monkeypatch):
    queries = _run_cooccurrence(gs, mock_metadata_store, monkeypatch, "true")
    cypher, params = queries[0]
    assert cypher.startswith(_RELATES_TO_CYPHER_TODAY)  # additive suffix only
    assert "r.created_at = coalesce(r.created_at, $ts)" in cypher
    assert "r.edge_uid = coalesce(r.edge_uid, $edge_uid)" in cypher
    assert "r.contradiction_count = coalesce(r.contradiction_count, 0)" in cypher
    # temporal stamps use to_graph_ts (fixed format), never _now_iso()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", params["ts"])
    assert params["edge_uid"]


def test_recurrence_resets_invalid_at_rule_r1(gs, mock_metadata_store, monkeypatch):
    queries = _run_cooccurrence(gs, mock_metadata_store, monkeypatch, "on")
    cypher, params = queries[0]
    # R1 with replay guard (verify-plan fix): an invalidated pair recurs ONLY
    # when the triggering evidence was recorded AFTER the invalidation
    # (r.invalid_at < $ev_ts) — webhook redelivery / reconcile replays of old
    # episodes must never resurrect a superseded fact.
    assert "WHEN r.invalid_at IS NOT NULL AND r.invalid_at < $ev_ts THEN $ev_ts" in cypher
    # replay case: invalidation NOT older than the evidence -> keep both
    assert "WHEN r.invalid_at IS NOT NULL THEN r.valid_at" in cypher
    assert (
        "r.invalid_at = CASE WHEN r.invalid_at IS NOT NULL "
        "AND r.invalid_at < $ev_ts THEN NULL ELSE r.invalid_at END" in cypher
    )
    # evidence timestamp is app-side normalised fixed format
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", params["ev_ts"])
    # ordering: the valid_at CASE must read invalid_at BEFORE the reset
    assert cypher.index("r.valid_at = CASE") < cypher.index("r.invalid_at = CASE")
    # contradiction_count is strictly increment-only: R1 must NOT reset it
    assert "r.contradiction_count = 0" not in cypher


def test_discussed_in_stamped_when_flag_on(gs, mock_metadata_store, monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    from graph import writer

    writer.project_session(
        gs,
        session_id="s1",
        summary="sum",
        topics=[],
        entities=[],
        entity_ids=["e1"],
        user_id="u1",
        ms=mock_metadata_store,
    )
    q = [(c, p) for c, p in gs.queries if "DISCUSSED_IN" in c][0]
    assert "r.valid_at = CASE WHEN r.valid_at IS NULL OR $ts < r.valid_at" in q[0]
    assert "r.edge_uid = coalesce(r.edge_uid, $edge_uid)" in q[0]


def test_discussed_in_cypher_identical_when_flag_off(gs, mock_metadata_store, monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    from graph import writer

    writer.project_session(
        gs,
        session_id="s1",
        summary="sum",
        topics=[],
        entities=[],
        entity_ids=["e1"],
        user_id="u1",
        ms=mock_metadata_store,
    )
    q = [(c, p) for c, p in gs.queries if "DISCUSSED_IN" in c][0]
    assert q[0] == (
        "MATCH (e) WHERE e.lumogis_id = $eid AND e.user_id = $uid "
        "MATCH (s:Session) WHERE s.lumogis_id = $sid "
        "MERGE (e)-[r:DISCUSSED_IN]->(s) "
        "SET r.timestamp = $now, r.user_id = $uid"
    )


def test_mentions_stamped_via_guarded_followup_when_flag_on(gs, mock_metadata_store, monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    mock_metadata_store._seed_fetch_one(None)  # memory props lookup
    mock_metadata_store._seed_fetch_all([])  # co-occurrence siblings
    from graph import writer

    writer.project_entity(
        gs,
        entity_id="e1",
        entity_type="PERSON",
        name="Ada",
        evidence_id="note-1",
        evidence_type="NOTE",
        user_id="u1",
        ms=mock_metadata_store,
    )
    stamps = [(c, p) for c, p in gs.queries if "MENTIONS {evidence_id" in c]
    assert len(stamps) == 1
    cypher, params = stamps[0]
    assert "MERGE" not in cypher  # guarded follow-up MATCHes the merged edge
    assert "r.valid_at = CASE WHEN r.valid_at IS NULL OR $ts < r.valid_at" in cypher
    assert params["eid"] == "note-1"


def test_mentions_no_followup_when_flag_off(gs, mock_metadata_store, monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    mock_metadata_store._seed_fetch_one(None)
    mock_metadata_store._seed_fetch_all([])
    from graph import writer

    writer.project_entity(
        gs,
        entity_id="e1",
        entity_type="PERSON",
        name="Ada",
        evidence_id="note-1",
        evidence_type="NOTE",
        user_id="u1",
        ms=mock_metadata_store,
    )
    assert not [c for c, _ in gs.queries if "MENTIONS {evidence_id" in c]


# ---------------------------------------------------------------------------
# Entity-merge transfer — full property map + RELATES_TO MERGE-by-pair
# (flag-independent: must hold once temporal properties exist)
# ---------------------------------------------------------------------------


def test_entity_merge_transfer_preserves_temporal_properties(gs):
    """A transferred MENTIONS edge keeps valid_at/edge_uid/contradiction_count
    AND invalid_at — an invalidated edge must not resurrect through a merge."""
    from graph import writer

    loser_props = {
        "evidence_id": "note-1",
        "user_id": "u1",
        "timestamp": "2026-01-01T00:00:00Z",
        "valid_at": "2025-06-01T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
        "invalid_at": "2026-02-01T00:00:00Z",
        "edge_uid": "uid-keep",
        "contradiction_count": 2,
    }
    gs.seed(
        "MATCH (loser)-[r]->(target)",
        [{"rel_type": "MENTIONS", "target_id": "t1", "props": dict(loser_props)}],
    )
    writer._transfer_outgoing_edges(gs, "winner", "loser", "u1")
    create_qs = [(c, p) for c, p in gs.queries if "CREATE" in c]
    assert len(create_qs) == 1
    cypher, params = create_qs[0]
    for key, val in loser_props.items():
        assert params.get(key) == val, f"transfer dropped {key}"


def test_entity_merge_leaves_exactly_one_relates_to_edge_per_pair(gs):
    """RELATES_TO transfer MERGEs by canonical pair and reconciles properties
    on collision (sum counts, earliest valid_at, winner edge_uid, invalid_at
    only when both carried it)."""
    from graph import writer

    gs.seed(
        "MATCH (loser)-[r]->(target)",
        [
            {
                "rel_type": "RELATES_TO",
                "target_id": "zzz-entity",
                "props": {
                    "co_occurrence_count": 3,
                    "last_seen_at": "2026-01-05T00:00:00+00:00",
                    "user_id": "u1",
                    "valid_at": "2025-01-01T00:00:00Z",
                    "created_at": "2025-01-01T00:00:00Z",
                    "invalid_at": "2026-02-01T00:00:00Z",  # loser was invalidated
                    "edge_uid": "loser-uid",
                    "contradiction_count": 1,
                },
            }
        ],
    )
    # winner already holds a VALID edge for the pair (no invalid_at)
    gs.seed(
        "MATCH (a)-[r:RELATES_TO]->(b) WHERE a.lumogis_id = $a_id",
        [
            {
                "props": {
                    "co_occurrence_count": 2,
                    "last_seen_at": "2026-03-01T00:00:00+00:00",
                    "user_id": "u1",
                    "valid_at": "2025-06-01T00:00:00Z",
                    "created_at": "2025-06-01T00:00:00Z",
                    "edge_uid": "winner-uid",
                    "contradiction_count": 0,
                }
            }
        ],
    )
    writer._transfer_outgoing_edges(gs, "winner-entity", "loser-entity", "u1")

    merges = [(c, p) for c, p in gs.queries if "MERGE (a)-[r:RELATES_TO]->(b)" in c]
    assert len(merges) == 1  # exactly one pair edge survives (MERGE, not CREATE)
    cypher, params = merges[0]
    assert "CREATE" not in cypher
    # canonical direction after winner substitution (winner-entity < zzz-entity)
    assert params["a_id"] == "winner-entity" and params["b_id"] == "zzz-entity"
    assert params["co_occurrence_count"] == 5  # summed
    assert params["valid_at"] == "2025-01-01T00:00:00Z"  # earliest kept
    assert params["created_at"] == "2025-01-01T00:00:00Z"
    assert params["edge_uid"] == "winner-uid"  # winner side kept on collision
    # invalid_at only if BOTH carried it — winner edge was valid, so dropped
    assert "invalid_at" not in params
    assert params["contradiction_count"] == 1  # max(1, 0) — history not lost
    # last_seen_at: later of the two, original string preserved
    assert params["last_seen_at"] == "2026-03-01T00:00:00+00:00"


def test_entity_merge_relates_to_no_collision_copies_props(gs):
    from graph import writer

    gs.seed(
        "MATCH (loser)-[r]->(target)",
        [
            {
                "rel_type": "RELATES_TO",
                "target_id": "aaa-entity",
                "props": {
                    "co_occurrence_count": 4,
                    "user_id": "u1",
                    "valid_at": "2025-01-01T00:00:00Z",
                    "invalid_at": "2026-01-01T00:00:00Z",
                    "edge_uid": "loser-uid",
                    "contradiction_count": 3,
                },
            }
        ],
    )
    # no existing winner-pair edge seeded → collision read returns []
    writer._transfer_outgoing_edges(gs, "winner-entity", "loser-entity", "u1")
    merges = [(c, p) for c, p in gs.queries if "MERGE (a)-[r:RELATES_TO]->(b)" in c]
    assert len(merges) == 1
    _, params = merges[0]
    # canonicalised: aaa-entity < winner-entity
    assert params["a_id"] == "aaa-entity" and params["b_id"] == "winner-entity"
    assert params["edge_uid"] == "loser-uid"  # copied unchanged, uid stays live
    assert params["invalid_at"] == "2026-01-01T00:00:00Z"  # no resurrection
    assert params["contradiction_count"] == 3


# ---------------------------------------------------------------------------
# Backfill (step 4) — batched, idempotent, app-side normalised
# ---------------------------------------------------------------------------


def test_backfill_normalises_bare_isoformat_last_seen_at(gs):
    """Legacy _now_iso() values (microseconds + offset) must be normalised
    app-side to the fixed format — a Cypher-side coalesce copy would seed a
    non-comparable mixed-format corpus."""
    from graph.reconcile import backfill_temporal_properties

    gs.seed(
        "MATCH ()-[r:RELATES_TO]->() WHERE r.valid_at IS NULL",
        [{"rid": 1, "last_seen": "2026-06-30T21:02:39.866123+00:00", "ts": None}],
    )
    result = backfill_temporal_properties(gs=gs, batch_size=500)
    writes = [(c, p) for c, p in gs.queries if "SET r.valid_at = coalesce(r.valid_at, $v)" in c]
    assert len(writes) == 1
    _, params = writes[0]
    assert params["v"] == "2026-06-30T21:02:39Z"  # fixed-format, byte-comparable
    assert params["uid"]
    assert result["edges_stamped"] == 1


def test_backfill_write_is_coalesce_guarded(gs):
    from graph.reconcile import backfill_temporal_properties

    gs.seed(
        "MATCH ()-[r:MENTIONS]->() WHERE r.valid_at IS NULL",
        [{"rid": 2, "last_seen": None, "ts": "2026-01-01T00:00:00Z"}],
    )
    backfill_temporal_properties(gs=gs, batch_size=500)
    cypher, params = [(c, p) for c, p in gs.queries if "SET r.valid_at = coalesce" in c][0]
    assert "r.created_at = coalesce(r.created_at, $v)" in cypher
    assert "r.edge_uid = coalesce(r.edge_uid, $uid)" in cypher
    assert "r.contradiction_count = coalesce(r.contradiction_count, 0)" in cypher
    assert params["v"] == "2026-01-01T00:00:00Z"  # timestamp fallback source


def test_backfill_edge_without_any_timestamp_falls_back_to_now(gs):
    from graph.reconcile import backfill_temporal_properties

    gs.seed(
        "MATCH ()-[r:DISCUSSED_IN]->() WHERE r.valid_at IS NULL",
        [{"rid": 3, "last_seen": None, "ts": None}],
    )
    backfill_temporal_properties(gs=gs, batch_size=500)
    _, params = [(c, p) for c, p in gs.queries if "SET r.valid_at = coalesce" in c][0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", params["v"])


def test_backfill_idempotent_second_run_noop(gs):
    """Second run: reads match nothing (valid_at now set) → zero writes."""
    from graph.reconcile import backfill_temporal_properties

    result = backfill_temporal_properties(gs=gs, batch_size=500)
    assert result["edges_stamped"] == 0
    assert not [c for c, _ in gs.queries if "SET r.valid_at = coalesce(r.valid_at, $v)" in c]


def test_backfill_terminates_on_static_batch(gs):
    """A write failure that leaves rows unstamped must not loop forever —
    the pass stops when a batch yields no previously-unseen edge ids."""
    from graph.reconcile import backfill_temporal_properties

    gs.seed(
        "MATCH ()-[r:RELATES_TO]->() WHERE r.valid_at IS NULL",
        [{"rid": 9, "last_seen": None, "ts": None}],
    )
    result = backfill_temporal_properties(gs=gs, batch_size=1)  # batch == rows
    assert result["edges_stamped"] == 1  # processed once, then terminated


def test_backfill_stamps_evidence_nodes_with_backfill_status(gs):
    from graph.reconcile import backfill_temporal_properties

    gs.seed("WHERE n.temporal_checked_at IS NULL", [{"cnt": 2}])
    result = backfill_temporal_properties(gs=gs, batch_size=500)
    stamp_qs = [(c, p) for c, p in gs.queries if "temporal_check_status" in c]
    assert stamp_qs, "expected evidence-node backfill stamping"
    for cypher, params in stamp_qs:
        assert params["status"] == "backfill"  # no LLM for historical corpus
    assert result["evidence_stamped"] >= 1


# ---------------------------------------------------------------------------
# Admin route — POST /graph/backfill/temporal
# ---------------------------------------------------------------------------


def _routes_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from graph.routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_temporal_backfill_route_403_without_admin_token(monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "s3cret-token-value")
    r = _routes_client().post("/graph/backfill/temporal")
    assert r.status_code == 403


def test_temporal_backfill_route_403_for_wrong_or_non_ascii_admin_token(monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "s3cret-token-value")
    for token in (b"wrong-token", "\xe9t\xe9".encode("latin-1")):
        r = _routes_client().post(
            "/graph/backfill/temporal", headers={"X-Graph-Admin-Token": token}
        )
        assert r.status_code == 403, token


def test_temporal_backfill_route_503_when_graph_absent(monkeypatch):
    import config as kg_config

    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    # get_graph_store() resolves the bank-scoped cache key; pre-seed it None
    kg_config._instances["graph_store:personal"] = None
    r = _routes_client().post("/graph/backfill/temporal")
    assert r.status_code == 503


def test_temporal_backfill_route_202_and_409_when_running(monkeypatch, gs):
    import config as kg_config

    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    # route the bank-scoped graph store at the recording fake (avoids the
    # lazily-constructed real adapter in the background task)
    kg_config._instances["graph_store:personal"] = gs
    from graph import routes as graph_routes

    client = _routes_client()
    r = client.post("/graph/backfill/temporal")
    assert r.status_code == 202
    assert r.json()["status"] == "temporal_backfill_started"
    # TestClient runs BackgroundTasks before returning, so the lock is
    # released again here; hold it manually to pin the 409 contract
    # (atomic acquire(blocking=False) — no check-then-set window).
    assert graph_routes._temporal_backfill_lock.acquire(blocking=False)
    try:
        r2 = client.post("/graph/backfill/temporal")
        assert r2.status_code == 409
    finally:
        graph_routes._temporal_backfill_lock.release()


# ---------------------------------------------------------------------------
# Read-side validity filter (step 5) — flag-gated, null-tolerant, as_of-aware
# ---------------------------------------------------------------------------


def _expected_ego_cypher_flag_off():
    """Rebuild today's ego_network Cypher from the same config getters."""
    from auth import UserContext
    from visibility import visible_cypher_fragment

    import config as kg_config

    cooc = kg_config.get_cooccurrence_threshold()
    eq = kg_config.get_graph_edge_quality_threshold()
    user = UserContext(user_id="u1")
    center_vis, _ = visible_cypher_fragment(user, alias="center")
    neighbor_vis, _ = visible_cypher_fragment(user, alias="neighbor")
    return (
        f"MATCH (center)-[r:RELATES_TO]-(neighbor) "
        f"WHERE center.lumogis_id = $eid "
        f"  AND {center_vis} "
        f"  AND {neighbor_vis} "
        f"  AND r.co_occurrence_count >= {cooc} "
        f"  AND (r.edge_quality IS NULL "
        f"       OR r.edge_quality >= {eq}) "
        f"RETURN neighbor.lumogis_id AS neighbor_id, "
        f"       neighbor.name AS neighbor_name, "
        f"       neighbor.entity_type AS neighbor_type, "
        f"       r.co_occurrence_count AS strength "
        f"ORDER BY strength DESC "
        f"LIMIT 10"
    )


def test_query_unchanged_flag_off(gs, monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    from graph.query import ego_network

    ego_network(gs, "e1", "u1")
    cypher, _ = gs.queries[0]
    assert cypher == _expected_ego_cypher_flag_off()  # byte-identical
    assert "invalid_at" not in cypher and "expired_at" not in cypher


def test_query_filters_invalid_edges_flag_on(gs, monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    from graph.query import ego_network

    ego_network(gs, "e1", "u1")
    cypher, params = gs.queries[0]
    assert "AND (r.invalid_at IS NULL) AND (r.expired_at IS NULL)" in cypher
    assert "temporal_as_of" not in params


def test_query_includes_null_property_legacy_edges(gs, monkeypatch):
    """Null-tolerance is structural: the predicate is IS NULL — FalkorDB
    property ABSENCE satisfies it, so un-stamped legacy edges always pass."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    from graph.query import ego_network

    ego_network(gs, "e1", "u1")
    cypher, _ = gs.queries[0]
    assert "(r.invalid_at IS NULL)" in cypher  # absence passes
    assert "r.invalid_at =" not in cypher  # no equality/sentinel comparison


def test_query_as_of_param_wired_but_defaults_to_now_semantics(gs, monkeypatch):
    """Checkpoint fold 2b: internal as_of accepted; route exposure deferred."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    from graph.query import ego_network

    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ego_network(gs, "e1", "u1", as_of=as_of)
    cypher, params = gs.queries[0]
    assert "(r.invalid_at IS NULL OR r.invalid_at > $temporal_as_of)" in cypher
    assert "(r.expired_at IS NULL OR r.expired_at > $temporal_as_of)" in cypher
    assert params["temporal_as_of"] == "2026-01-01T00:00:00Z"


def test_mention_sources_filters_when_flag_on(gs, monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    from graph.query import mention_sources

    mention_sources(gs, "e1", "u1")
    cypher, _ = gs.queries[0]
    assert "AND (r.invalid_at IS NULL) AND (r.expired_at IS NULL)" in cypher


def test_mention_sources_unchanged_flag_off(gs, monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    from graph.query import mention_sources

    mention_sources(gs, "e1", "u1")
    cypher, _ = gs.queries[0]
    assert "invalid_at" not in cypher and "expired_at" not in cypher


def test_backfill_evidence_pass_respects_recency_cutoff(gs):
    """verify-plan fix: 'unstamped' doubles as the sweep-retry marker, so the
    backfill must only stamp nodes OLDER than the cutoff — a nightly reconcile
    must not convert pending retries into permanently skipped episodes."""
    from graph.reconcile import backfill_temporal_properties

    gs.seed("WHERE n.temporal_checked_at IS NULL", [{"cnt": 0}])
    backfill_temporal_properties(gs=gs, batch_size=500)
    stamp_qs = [(c, p) for c, p in gs.queries if "temporal_check_status" in c]
    assert stamp_qs
    for cypher, params in stamp_qs:
        assert "< $cutoff" in cypher
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", params["cutoff"])
