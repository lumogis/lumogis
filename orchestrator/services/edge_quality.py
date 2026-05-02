# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Edge quality scoring — Pass 3 of the KG Quality Pipeline.

Computes PPMI-based co-occurrence scores and exponential temporal decay for
entity pairs that share evidence.  Writes results to edge_scores (Postgres)
and optionally updates RELATES_TO edge properties in FalkorDB.

PPMI (Positive Pointwise Mutual Information)
--------------------------------------------
Applied only to entity pairs that already co-occur in entity_relations —
not a full N×N cross-product.  Co-occurrence is evidence-based: two entities
co-occur when they both appear in the same evidence_id (session or document).

Dedup contract: as of migration 012, entity_relations has a UNIQUE index on
(source_id, evidence_id, relation_type, user_id), and all writers go through
INSERT … ON CONFLICT DO NOTHING (see services/entities.py). The COUNT(DISTINCT
evidence_id) guards in this module's PPMI queries are therefore defence in
depth, not the primary deduplication mechanism — they remain because they
correctly express the intent ("count each evidence once") even if a future
relaxation of the unique constraint were to permit duplicates again.

  P(x)   = distinct evidence_ids containing x  /  total distinct evidence_ids
  P(x,y) = distinct evidence_ids containing both x AND y  /  total distinct evidence_ids
  PPMI(x,y) = max(0, log2(P(x,y) / (P(x) * P(y))))

Evidence granularity weights modulate the effective co-occurrence count:
  sentence  → 1.0
  paragraph → 0.7
  document  → 0.4  (default for all rows prior to Pass 3)

Temporal decay
--------------
  decay_factor(t) = 0.5 ^ (days_since_last_evidence / half_life_days)

Half-lives (configurable via env):
  DECAY_HALF_LIFE_RELATES_TO   default 365 days
  DECAY_HALF_LIFE_MENTIONS     default 180 days  (reserved for future use)
  DECAY_HALF_LIFE_DISCUSSED_IN default 30 days   (reserved for future use)

Composite edge quality score
-----------------------------
  edge_quality = 0.25 * normalised_frequency
               + 0.35 * ppmi_score_normalised
               + 0.20 * window_weight
               + 0.20 * decay_factor

Weights sum to 1.0.  Normalisation uses the max value across all pairs for
the same user; when only one pair exists the normalised terms are 0.0, which
is correct (relative signal is undefined with a single pair).

Public entry points
-------------------
  run_edge_quality_job(user_id)  — compute + upsert edge_scores + update FalkorDB
  run_weekly_quality_job()       — full weekly maintenance: edge scores + corpus constraints

Never raises — all exceptions are caught and logged.  Returns a summary dict.
"""

import logging
import math
import time
from datetime import datetime
from datetime import timezone

import config

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Granularity weights
# ---------------------------------------------------------------------------

_GRANULARITY_WEIGHTS: dict[str, float] = {
    "sentence": 1.0,
    "paragraph": 0.7,
    "document": 0.4,
}
_DEFAULT_GRANULARITY_WEIGHT = 0.4  # matches 'document' default for pre-Pass-3 rows


def _half_life_relates_to() -> float:
    return float(config.get_decay_half_life_relates_to())


# ---------------------------------------------------------------------------
# Temporal decay
# ---------------------------------------------------------------------------


def compute_decay_factor(last_evidence_at: datetime | None, half_life_days: float) -> float:
    """Return 0.5^(elapsed_days / half_life_days).

    Returns 1.0 if last_evidence_at is None (no evidence yet — no penalty).
    Clamps result to [0.0, 1.0].
    """
    if last_evidence_at is None:
        return 1.0
    if half_life_days <= 0:
        return 1.0
    now = datetime.now(timezone.utc)
    # Make last_evidence_at timezone-aware if it isn't (legacy rows may be naive)
    if last_evidence_at.tzinfo is None:
        last_evidence_at = last_evidence_at.replace(tzinfo=timezone.utc)
    elapsed_days = max(0.0, (now - last_evidence_at).total_seconds() / 86400.0)
    decay = 0.5 ** (elapsed_days / half_life_days)
    return max(0.0, min(1.0, decay))


# ---------------------------------------------------------------------------
# PPMI computation
# ---------------------------------------------------------------------------


def compute_ppmi(
    pair_count: int,
    count_a: int,
    count_b: int,
    total_evidence: int,
) -> float:
    """Compute PPMI(a, b) from raw co-occurrence counts.

    All counts refer to distinct evidence_id values:
      pair_count     — evidence_ids containing both a and b
      count_a        — evidence_ids containing a
      count_b        — evidence_ids containing b
      total_evidence — total distinct evidence_ids in the corpus

    Returns max(0, log2(P(a,b) / (P(a) * P(b)))).
    Returns 0.0 if any count is 0 or total_evidence is 0.
    """
    if total_evidence <= 0 or pair_count <= 0 or count_a <= 0 or count_b <= 0:
        return 0.0
    p_xy = pair_count / total_evidence
    p_x = count_a / total_evidence
    p_y = count_b / total_evidence
    denom = p_x * p_y
    if denom <= 0:
        return 0.0
    pmi = math.log2(p_xy / denom)
    return max(0.0, pmi)


# ---------------------------------------------------------------------------
# Data fetching helpers
# ---------------------------------------------------------------------------


def _fetch_cooccurrence_data(ms, user_id: str) -> dict:
    """Fetch all co-occurrence data needed for PPMI from Postgres.

    Returns a dict with:
      total_evidence   : int — distinct evidence_ids for user
      entity_counts    : {entity_id: int} — distinct evidence_ids per entity
      pair_data        : {(eid_a, eid_b): dict} — per-pair aggregates
        pair_data[pair] keys: count (int), weighted_count (float),
                              last_evidence_at (datetime|None)

    entity_id_a < entity_id_b always (canonical ordering, matching writer.py).
    Never raises — returns empty structure on any error.
    """
    empty = {"total_evidence": 0, "entity_counts": {}, "pair_data": {}}
    try:
        # SCOPE-EXEMPT: `entity_relations` carries no `scope` column per plan
        # §2.4 rule 9 — relation visibility inherits from endpoint entities.
        # Quality scoring computes per-user statistics on the user's own
        # relations; cross-user joins would corrupt the score.
        row = ms.fetch_one(
            "SELECT COUNT(DISTINCT evidence_id) AS cnt FROM entity_relations WHERE user_id = %s",
            (user_id,),
        )
        total_evidence = int(row["cnt"]) if row and row.get("cnt") else 0
        if total_evidence == 0:
            return empty

        # SCOPE-EXEMPT: see above — `entity_relations` has no scope column.
        entity_rows = ms.fetch_all(
            "SELECT source_id, COUNT(DISTINCT evidence_id) AS cnt "
            "FROM entity_relations WHERE user_id = %s "
            "GROUP BY source_id",
            (user_id,),
        )
        entity_counts = {str(r["source_id"]): int(r["cnt"]) for r in entity_rows}

        # Co-occurring pairs: entities sharing the same evidence_id
        # We join entity_relations to itself on evidence_id, filtering a < b to
        # avoid double-counting.  Also fetch granularity and last seen timestamp.
        # SCOPE-EXEMPT: see above — `entity_relations` has no scope column;
        # the self-join enforces same-user pairing on both sides.
        pair_rows = ms.fetch_all(
            "SELECT "
            "  er_a.source_id AS eid_a, "
            "  er_b.source_id AS eid_b, "
            "  COUNT(DISTINCT er_a.evidence_id) AS pair_count, "
            "  MAX(GREATEST(er_a.created_at, er_b.created_at)) AS last_evidence_at, "
            "  er_a.evidence_granularity AS gran_a, "
            "  er_b.evidence_granularity AS gran_b "
            "FROM entity_relations er_a "
            "JOIN entity_relations er_b "
            "  ON er_a.evidence_id = er_b.evidence_id "
            "  AND er_a.user_id = er_b.user_id "
            "  AND er_a.source_id < er_b.source_id "
            # SCOPE-EXEMPT: per-user pair-stats over scope-less entity_relations.
            "WHERE er_a.user_id = %s "
            "GROUP BY er_a.source_id, er_b.source_id, "
            "         er_a.evidence_granularity, er_b.evidence_granularity",
            (user_id,),
        )

        # Aggregate multiple granularity rows for the same pair
        pair_data: dict[tuple, dict] = {}
        for r in pair_rows:
            eid_a = str(r["eid_a"])
            eid_b = str(r["eid_b"])
            pair = (eid_a, eid_b)
            count = int(r["pair_count"])
            gran_weight = _GRANULARITY_WEIGHTS.get(
                str(r.get("gran_a") or "document"),
                _DEFAULT_GRANULARITY_WEIGHT,
            )
            gran_weight_b = _GRANULARITY_WEIGHTS.get(
                str(r.get("gran_b") or "document"),
                _DEFAULT_GRANULARITY_WEIGHT,
            )
            # Use the average of both sides as the effective granularity weight
            effective_gran = (gran_weight + gran_weight_b) / 2.0
            weighted = count * effective_gran

            last_at = r.get("last_evidence_at")

            if pair not in pair_data:
                pair_data[pair] = {
                    "count": 0,
                    "weighted_count": 0.0,
                    "last_evidence_at": None,
                    "total_gran_weight": 0.0,
                    "gran_rows": 0,
                }
            entry = pair_data[pair]
            entry["count"] += count
            entry["weighted_count"] += weighted
            entry["total_gran_weight"] += effective_gran * count
            entry["gran_rows"] += count
            if last_at is not None:
                if entry["last_evidence_at"] is None or last_at > entry["last_evidence_at"]:
                    entry["last_evidence_at"] = last_at

        # Compute the window_weight (weighted average granularity) per pair
        for entry in pair_data.values():
            if entry["gran_rows"] > 0:
                entry["window_weight"] = entry["total_gran_weight"] / entry["gran_rows"]
            else:
                entry["window_weight"] = _DEFAULT_GRANULARITY_WEIGHT

        return {
            "total_evidence": total_evidence,
            "entity_counts": entity_counts,
            "pair_data": pair_data,
        }
    except Exception:
        _log.exception("edge_quality: _fetch_cooccurrence_data failed for user_id=%s", user_id)
        return empty


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------


def _compute_scores(cooc: dict) -> list[dict]:
    """Convert co-occurrence data into a list of scored pair dicts.

    Each dict has: entity_id_a, entity_id_b, ppmi_score, edge_quality,
    decay_factor, window_weight, last_evidence_at.
    Returns [] on empty input.
    """
    total_evidence = cooc["total_evidence"]
    entity_counts = cooc["entity_counts"]
    pair_data = cooc["pair_data"]

    if not pair_data:
        return []

    half_life = _half_life_relates_to()

    # First pass: compute raw PPMI and decay per pair
    raw_pairs: list[dict] = []
    for (eid_a, eid_b), entry in pair_data.items():
        count_a = entity_counts.get(eid_a, 0)
        count_b = entity_counts.get(eid_b, 0)
        ppmi = compute_ppmi(entry["count"], count_a, count_b, total_evidence)
        decay = compute_decay_factor(entry.get("last_evidence_at"), half_life)
        raw_pairs.append(
            {
                "entity_id_a": eid_a,
                "entity_id_b": eid_b,
                "raw_count": entry["count"],
                "weighted_count": entry["weighted_count"],
                "ppmi_score": ppmi,
                "decay_factor": decay,
                "window_weight": entry.get("window_weight", _DEFAULT_GRANULARITY_WEIGHT),
                "last_evidence_at": entry.get("last_evidence_at"),
            }
        )

    if not raw_pairs:
        return []

    # Normalisation denominators (max across all pairs for this user)
    max_count = max(p["raw_count"] for p in raw_pairs)
    max_ppmi = max(p["ppmi_score"] for p in raw_pairs)

    scored: list[dict] = []
    for p in raw_pairs:
        norm_freq = (p["raw_count"] / max_count) if max_count > 0 else 0.0
        norm_ppmi = (p["ppmi_score"] / max_ppmi) if max_ppmi > 0 else 0.0

        # Composite score — weights sum to 1.0
        edge_quality = (
            0.25 * norm_freq
            + 0.35 * norm_ppmi
            + 0.20 * p["window_weight"]
            + 0.20 * p["decay_factor"]
        )
        edge_quality = max(0.0, min(1.0, edge_quality))

        scored.append(
            {
                "entity_id_a": p["entity_id_a"],
                "entity_id_b": p["entity_id_b"],
                "ppmi_score": p["ppmi_score"],
                "edge_quality": edge_quality,
                "decay_factor": p["decay_factor"],
                "window_weight": p["window_weight"],
                "last_evidence_at": p["last_evidence_at"],
            }
        )

    return scored


# ---------------------------------------------------------------------------
# FalkorDB update
# ---------------------------------------------------------------------------


def _update_falkordb(scored_pairs: list[dict], user_id: str) -> int:
    """Push edge_quality scores to RELATES_TO edges in FalkorDB.

    Uses canonical direction: entity_id_a → entity_id_b (a < b, matching writer.py).
    Matches on both directions (undirected) so the MERGE finds the existing edge.

    Returns count of edges updated.  Logs WARNING on unavailability.
    Never raises.
    """
    gs = config.get_graph_store()
    if gs is None:
        _log.warning(
            "edge_quality: FalkorDB unavailable — Postgres edge_scores written, "
            "FalkorDB RELATES_TO properties not updated"
        )
        return 0

    updated = 0
    for p in scored_pairs:
        try:
            # Match undirected — writer stores a→b but we want to update regardless of stored direction
            cypher = (
                "MATCH (a)-[r:RELATES_TO]-(b) "
                "WHERE a.lumogis_id = $a_id AND a.user_id = $uid "
                "  AND b.lumogis_id = $b_id AND b.user_id = $uid "
                "SET r.ppmi_score = $ppmi, "
                "    r.edge_quality = $eq, "
                "    r.decay_factor = $decay, "
                "    r.last_evidence_at = $last_at"
            )
            last_at = p["last_evidence_at"]
            last_at_iso = last_at.isoformat() if last_at is not None else None
            gs.query(
                cypher,
                {
                    "a_id": p["entity_id_a"],
                    "b_id": p["entity_id_b"],
                    "uid": user_id,
                    "ppmi": p["ppmi_score"],
                    "eq": p["edge_quality"],
                    "decay": p["decay_factor"],
                    "last_at": last_at_iso,
                },
            )
            updated += 1
        except Exception:
            _log.exception(
                "edge_quality: FalkorDB update failed a=%s b=%s",
                p["entity_id_a"],
                p["entity_id_b"],
            )
    return updated


# ---------------------------------------------------------------------------
# Postgres upsert
# ---------------------------------------------------------------------------


def _upsert_edge_scores(ms, scored_pairs: list[dict], user_id: str) -> int:
    """Upsert scored pairs into edge_scores table.  Returns count upserted."""
    upserted = 0
    for p in scored_pairs:
        try:
            ms.execute(
                "INSERT INTO edge_scores "
                "  (user_id, entity_id_a, entity_id_b, ppmi_score, edge_quality, "
                "   decay_factor, last_evidence_at, computed_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW()) "
                "ON CONFLICT (user_id, entity_id_a, entity_id_b) DO UPDATE SET "
                "  ppmi_score       = EXCLUDED.ppmi_score, "
                "  edge_quality     = EXCLUDED.edge_quality, "
                "  decay_factor     = EXCLUDED.decay_factor, "
                "  last_evidence_at = EXCLUDED.last_evidence_at, "
                "  computed_at      = NOW()",
                (
                    user_id,
                    p["entity_id_a"],
                    p["entity_id_b"],
                    p["ppmi_score"],
                    p["edge_quality"],
                    p["decay_factor"],
                    p["last_evidence_at"],
                ),
            )
            upserted += 1
        except Exception:
            _log.exception(
                "edge_quality: upsert failed a=%s b=%s",
                p["entity_id_a"],
                p["entity_id_b"],
            )
    return upserted


# ---------------------------------------------------------------------------
# Public entry point — edge scoring job
# ---------------------------------------------------------------------------


def run_edge_quality_job(user_id: str = "default") -> dict:
    """Compute PPMI + decay + edge_quality for all co-occurring entity pairs.

    Upserts results into edge_scores table.  If FalkorDB is available, also
    updates RELATES_TO edge properties.

    Returns summary: {pairs_computed, pairs_upserted, falkordb_updated, duration_ms}
    Never raises — logs ERROR on exception and returns empty summary.
    """
    empty = {"pairs_computed": 0, "pairs_upserted": 0, "falkordb_updated": 0, "duration_ms": 0}
    t0 = time.monotonic()
    try:
        ms = config.get_metadata_store()
        cooc = _fetch_cooccurrence_data(ms, user_id)
        scored = _compute_scores(cooc)
        pairs_computed = len(scored)

        pairs_upserted = _upsert_edge_scores(ms, scored, user_id)
        falkordb_updated = _update_falkordb(scored, user_id)

        duration_ms = int((time.monotonic() - t0) * 1000)
        _log.info(
            "component=edge_quality user_id=%s pairs_computed=%d pairs_upserted=%d "
            "falkordb_updated=%d duration_ms=%d",
            user_id,
            pairs_computed,
            pairs_upserted,
            falkordb_updated,
            duration_ms,
        )
        return {
            "pairs_computed": pairs_computed,
            "pairs_upserted": pairs_upserted,
            "falkordb_updated": falkordb_updated,
            "duration_ms": duration_ms,
        }
    except Exception:
        _log.error(
            "run_edge_quality_job: unexpected error for user_id=%s — returning empty summary",
            user_id,
            exc_info=True,
        )
        return {**empty, "duration_ms": int((time.monotonic() - t0) * 1000)}


# ---------------------------------------------------------------------------
# Weekly maintenance job
# ---------------------------------------------------------------------------


def run_weekly_quality_job() -> dict:
    """Weekly KG quality maintenance. Runs in order:

      1. Edge quality + PPMI scoring (run_edge_quality_job)
      2. Corpus-level constraint checks: orphan sweep + alias uniqueness
      3. Probabilistic deduplication (run_deduplication_job) — Pass 4b

    Logs a structured summary at INFO level on completion.
    Never raises — exceptions in one component do not prevent others from running.
    The deduplication step is independently guarded so a failure there cannot
    prevent steps 1 or 2 from completing.

    Returns combined summary dict.
    """
    t0 = time.monotonic()
    _log.info("component=quality_maintenance starting weekly quality job")

    # 1. Edge quality scoring
    edge_summary: dict = {
        "pairs_computed": 0,
        "pairs_upserted": 0,
        "falkordb_updated": 0,
        "duration_ms": 0,
    }
    try:
        edge_summary = run_edge_quality_job(user_id="default")
    except Exception:
        _log.error("run_weekly_quality_job: edge_quality step failed", exc_info=True)

    # 2. Corpus-level constraint checks
    orphan_violations = 0
    alias_violations = 0
    try:
        from services.entity_constraints import check_orphan_entities

        orphan_violations = check_orphan_entities(user_id="default")
    except Exception:
        _log.error("run_weekly_quality_job: orphan_entity check failed", exc_info=True)

    try:
        from services.entity_constraints import check_alias_uniqueness

        alias_violations = check_alias_uniqueness(user_id="default")
    except Exception:
        _log.error("run_weekly_quality_job: alias_uniqueness check failed", exc_info=True)

    # 3. Probabilistic deduplication (Pass 4b) — must run after steps 1 and 2
    dedup_auto_merged = 0
    dedup_queued_for_review = 0
    try:
        from services.deduplication import run_deduplication_job

        dedup_summary = run_deduplication_job(user_id="default")
        dedup_auto_merged = dedup_summary.get("auto_merged", 0)
        dedup_queued_for_review = dedup_summary.get("queued_for_review", 0)
    except Exception:
        _log.error("run_weekly_quality_job: deduplication step failed", exc_info=True)

    duration_ms = int((time.monotonic() - t0) * 1000)

    _log.info(
        "component=quality_maintenance pairs_computed=%d pairs_upserted=%d "
        "orphan_violations=%d alias_violations=%d "
        "auto_merged=%d queued_for_review=%d duration_ms=%d",
        edge_summary.get("pairs_computed", 0),
        edge_summary.get("pairs_upserted", 0),
        orphan_violations,
        alias_violations,
        dedup_auto_merged,
        dedup_queued_for_review,
        duration_ms,
    )

    # Write completion timestamp to kg_settings for the job-status endpoint.
    from datetime import datetime
    from datetime import timezone

    try:
        ms = config.get_metadata_store()
        now_iso = datetime.now(timezone.utc).isoformat()
        ms.execute(
            "INSERT INTO kg_settings (key, value) VALUES ('_job_last_weekly', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (now_iso,),
        )
    except Exception:
        _log.warning(
            "run_weekly_quality_job: failed to write _job_last_weekly timestamp to kg_settings",
            exc_info=True,
        )

    return {
        "pairs_computed": edge_summary.get("pairs_computed", 0),
        "pairs_upserted": edge_summary.get("pairs_upserted", 0),
        "falkordb_updated": edge_summary.get("falkordb_updated", 0),
        "orphan_violations": orphan_violations,
        "alias_violations": alias_violations,
        "auto_merged": dedup_auto_merged,
        "queued_for_review": dedup_queued_for_review,
        "duration_ms": duration_ms,
    }
