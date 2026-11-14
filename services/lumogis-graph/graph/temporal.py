# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Temporal graph mechanics for LUM-104 — LLM-free, runs in the KG service.

Two-axis temporal validity on KG edges:

  valid_at    — real-world truth time (LLM-extracted Core-side, or ingestion
                time by default)
  invalid_at  — set when a fact is superseded (contradiction auto-apply);
                never removed by ordinary writes (rule R1 recurrence is the
                sole exception, applied by the writer)
  expired_at  — reserved for purge/expiry flows; filtered on reads
  created_at  — edge ingestion time; stamped once, never updated

This module owns the timezone/format discipline (the #893 pattern): every
datetime that enters a comparison or a graph property passes through
:func:`ensure_utc` / :func:`to_graph_ts` / :func:`parse_graph_ts`. The fixed
``%Y-%m-%dT%H:%M:%SZ`` storage format makes lexicographic comparison in
Cypher chronologically correct — never copy the writer's legacy
``_now_iso()`` values (variable width, microseconds, ``+00:00`` offset) into
a temporal property without normalising through these helpers.

NULL semantics (FalkorDB): graph properties cannot hold a literal NULL —
"NULL" is property ABSENCE, and ``r.invalid_at IS NULL`` is true for an
absent property. ``SET r.x = NULL`` removes the property. No writer may emit
``invalid_at: ""`` or any sentinel string.

Invalidation writes MATCH by ``edge_uid`` and never MERGE (the #1001
pattern): an invalidation must never create an edge or run against a missing
node — a missing match is skip-and-log, decided by the caller via the return
value.

LLM boundary: this module is data-only. All LLM stages (valid_at extraction,
contradiction judgment) live Core-side in ``orchestrator/plugins/graph/``,
preserving the KG-service invariant ("KG never invokes an LLM" — see
config.py module docstring).

Log policy: never log raw episode text; ids, counts, truncated reasons only.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from graph.schema import EVIDENCE_TYPE_LABEL_MAP
from graph.schema import MAX_TEXT_LENGTH
from graph.schema import PERSONAL_SCOPE
from graph.schema import EdgeType

_log = logging.getLogger(__name__)

# Fixed-width UTC storage format — lexicographic order == chronological order.
GRAPH_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# ---------------------------------------------------------------------------
# Temporal check status — the single authoritative enum, shared verbatim by
# the evidence-node property `temporal_check_status` and the Core-side
# pipeline's TemporalCheckResult.status (pinned by test).
# ---------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_EXTRACT_FAILED = "extract_failed"  # informational — judgment still ran
STATUS_JUDGE_FAILED = "judge_failed"
STATUS_TEXT_UNAVAILABLE = "text_unavailable"  # terminal
STATUS_SKIPPED = "skipped"  # terminal — zero graph entities
STATUS_BACKFILL = "backfill"  # stamped by historical backfill, no LLM

TEMPORAL_CHECK_STATUSES: tuple[str, ...] = (
    STATUS_OK,
    STATUS_EXTRACT_FAILED,
    STATUS_JUDGE_FAILED,
    STATUS_TEXT_UNAVAILABLE,
    STATUS_SKIPPED,
    STATUS_BACKFILL,
)

# Evidence-node property names (schema contract, LUM-104).
PROP_TEMPORAL_CHECKED_AT = "temporal_checked_at"
PROP_TEMPORAL_CHECK_STATUS = "temporal_check_status"


# ---------------------------------------------------------------------------
# Feature flag — KG-side reader of LUMOGIS_FF_TEMPORAL_KG.
# ---------------------------------------------------------------------------

# Replicates orchestrator/features.py `_TRUTHY` EXACTLY (case-insensitive,
# stripped) so `LUMOGIS_FF_TEMPORAL_KG=on` cannot be honoured Core-side and
# ignored service-side. Pinned by a shared-fixture test on both sides.
_TRUTHY = frozenset({"true", "1", "yes", "on"})

TEMPORAL_FLAG_ENV = "LUMOGIS_FF_TEMPORAL_KG"


def temporal_feature_enabled() -> bool:
    """Return whether the LUM-104 temporal feature is enabled (default off)."""
    raw = os.environ.get(TEMPORAL_FLAG_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# tz/format helpers — everything passes through these (the #893 pattern)
# ---------------------------------------------------------------------------


def ensure_utc(dt: datetime) -> datetime:
    """Return an aware-UTC datetime: naive → assume UTC; aware → convert."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def now_utc() -> datetime:
    """Aware-UTC now — the only sanctioned clock read in temporal code."""
    return datetime.now(timezone.utc)


def to_graph_ts(dt: datetime) -> str:
    """Serialize to the fixed-width UTC graph format (microseconds dropped)."""
    return ensure_utc(dt).strftime(GRAPH_TS_FORMAT)


def parse_graph_ts(s: str) -> datetime:
    """Parse a graph timestamp; always returns aware-UTC.

    Accepts the fixed format (``Z`` suffix), ``+00:00``-style offsets, and
    legacy bare-isoformat values (microseconds + offset) as written by the
    writer's ``_now_iso()``. Naive strings are assumed UTC.
    """
    raw = s.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    # This IS the sanctioned wrapper; all other temporal code must call
    # parse_graph_ts instead (grep-gate enforced).
    dt = datetime.fromisoformat(raw)  # gate-allow: fromisoformat
    return ensure_utc(dt)


# ---------------------------------------------------------------------------
# Verdict dataclass (KG-side mechanics shape; the Core pipeline's Pydantic
# ContradictionVerdict validates LLM output and converts to this).
# ---------------------------------------------------------------------------

VERDICT_SUPERSEDES = "supersedes"
VERDICT_COMPATIBLE = "compatible"
VERDICT_UNRELATED = "unrelated"


@dataclass(frozen=True)
class TemporalVerdict:
    """One contradiction judgment for a candidate edge."""

    edge_uid: str
    verdict: str  # supersedes | compatible | unrelated
    confidence: float
    reason: str = ""


# ---------------------------------------------------------------------------
# Edge stamping — the min-rule
# ---------------------------------------------------------------------------

# valid_at means "when the fact became true": for a currently-valid edge the
# correct value is the EARLIEST known signal, so the stamp only ever moves
# valid_at backward (idempotent; safe on shared co-occurrence edges). Rule R1
# (recurrence after invalidation, applied in the writer) is the one exception
# that rewrites valid_at forward. Lexicographic `<` is safe on the fixed
# format; scalar CASE has production precedent (projection.py:166) and does
# not hit the CASE-bound-node-variable caveat.
_MIN_RULE_SET = (
    "SET r.valid_at = CASE WHEN r.valid_at IS NULL OR $v < r.valid_at "
    "THEN $v ELSE r.valid_at END, "
    "r.created_at = coalesce(r.created_at, $now_ts), "
    "r.contradiction_count = coalesce(r.contradiction_count, 0)"
)


def _stamp_matched_edges(gs, match_where: str, params: dict, valid_at: datetime) -> int:
    """Two-phase stamp for edges matched by ``match_where``.

    Phase A reads matched edges (internal id + edge_uid); phase B assigns a
    fresh app-side uuid4 to each edge missing ``edge_uid`` (never a shared
    uid from a single Cypher SET) and applies the min-rule. Returns the
    number of edges stamped.
    """
    v = to_graph_ts(valid_at)
    now_ts = to_graph_ts(now_utc())
    rows = gs.query(
        f"{match_where} RETURN id(r) AS rid, r.edge_uid AS edge_uid",
        params,
    )
    stamped = 0
    for row in rows or []:
        rid = row.get("rid")
        if rid is None:
            continue
        edge_uid = row.get("edge_uid") or uuid.uuid4().hex
        gs.query(
            "MATCH ()-[r]->() WHERE id(r) = $rid "
            f"{_MIN_RULE_SET}, r.edge_uid = coalesce(r.edge_uid, $edge_uid)",
            {"rid": rid, "v": v, "now_ts": now_ts, "edge_uid": edge_uid},
        )
        stamped += 1
    return stamped


def stamp_valid_at_for_evidence(
    gs,
    *,
    evidence_id: str,
    valid_at: datetime,
    user_id: str,
) -> int:
    """Apply the min-rule to MENTIONS/DISCUSSED_IN edges of one episode.

    MENTIONS edges are matched directly by their ``evidence_id`` property;
    DISCUSSED_IN edges carry no evidence_id, so they are matched via the
    Session node's ``lumogis_id`` (for session episodes the evidence id IS
    the session id). RELATES_TO edges carry neither — use
    :func:`stamp_valid_at_for_pairs` with the Postgres-derived pair set.
    Returns the number of edges stamped.
    """
    stamped = _stamp_matched_edges(
        gs,
        f"MATCH ()-[r:{EdgeType.MENTIONS}]->() WHERE r.evidence_id = $eid AND r.user_id = $uid",
        {"eid": evidence_id, "uid": user_id},
        valid_at,
    )
    stamped += _stamp_matched_edges(
        gs,
        f"MATCH ()-[r:{EdgeType.DISCUSSED_IN}]->(s) WHERE s.lumogis_id = $eid AND r.user_id = $uid",
        {"eid": evidence_id, "uid": user_id},
        valid_at,
    )
    return stamped


def stamp_valid_at_for_pairs(
    gs,
    *,
    pairs: list[tuple[str, str]],
    valid_at: datetime,
    user_id: str,
) -> int:
    """Apply the min-rule to RELATES_TO edges for canonical entity pairs.

    ``pairs`` are (a_id, b_id) tuples; each is re-canonicalised here
    (lower→higher lumogis_id, ADR-038) so callers cannot accidentally miss
    the directed MERGE'd edge. Returns the number of edges stamped.
    """
    stamped = 0
    for a_id, b_id in pairs:
        lo, hi = (a_id, b_id) if a_id < b_id else (b_id, a_id)
        stamped += _stamp_matched_edges(
            gs,
            f"MATCH (a)-[r:{EdgeType.RELATES_TO}]->(b) "
            "WHERE a.lumogis_id = $a_id AND b.lumogis_id = $b_id "
            "AND r.user_id = $uid",
            {"a_id": lo, "b_id": hi, "uid": user_id},
            valid_at,
        )
    return stamped


# ---------------------------------------------------------------------------
# Invalidation write (MATCH, never MERGE — the #1001 pattern)
# ---------------------------------------------------------------------------


def apply_invalidation(
    gs,
    *,
    edge_uid: str,
    invalid_at: datetime,
    invalidated_by_evidence_id: str,
    reason: str,
    user_id: str,
    superseded_by_edge_uid: str | None = None,
) -> bool:
    """Soft-invalidate the edge identified by ``edge_uid``.

    ``invalid_at`` is the superseding episode's valid_at (real-world
    supersession time), not wall-clock apply time — the two-axis semantics.
    MATCHes the existing edge only; a missing match returns False so the
    caller can skip-and-log (never create). Never deletes anything.
    """
    extra = ""
    params = {
        "edge_uid": edge_uid,
        "uid": user_id,
        "invalid_at": to_graph_ts(invalid_at),
        "ev": invalidated_by_evidence_id,
        "reason": (reason or "")[:MAX_TEXT_LENGTH],
    }
    if superseded_by_edge_uid:
        extra = ", r.superseded_by_edge_uid = $sup_uid"
        params["sup_uid"] = superseded_by_edge_uid
    rows = gs.query(
        "MATCH ()-[r]->() WHERE r.edge_uid = $edge_uid AND r.user_id = $uid "
        "SET r.invalid_at = $invalid_at, "
        "r.invalidated_by_evidence_id = $ev, "
        "r.invalidation_reason = $reason, "
        "r.contradiction_count = coalesce(r.contradiction_count, 0) + 1"
        f"{extra} "
        "RETURN id(r) AS rid",
        params,
    )
    if not rows:
        _log.info("temporal: invalidation target edge_uid=%s not found — skipped", edge_uid)
        return False
    return True


# ---------------------------------------------------------------------------
# SUPERSEDES recording (applied supersessions AND shadow-mode proposals)
# ---------------------------------------------------------------------------


def _merge_evidence_node(gs, evidence_id: str, evidence_type: str, user_id: str) -> str | None:
    """Merge (or fetch) the evidence-object node; None for unknown types.

    Same pattern as the writer's ``_ensure_source_node`` (label map shared
    via schema.EVIDENCE_TYPE_LABEL_MAP to avoid a writer import cycle).

    The node is ``scope='personal'`` (LUM-961).  ``create_node`` SETs ``scope``
    on an existing node with the same ``(lumogis_id, user_id)``, so
    ``evidence_id`` must be a personal row id, never a shared/system projection
    id.  Superseded-evidence ids come from ``entity_relations`` rows of the
    episode owner (``temporal_pipeline._latest_shared_evidence_for_pair``),
    which are personal.
    """
    label = EVIDENCE_TYPE_LABEL_MAP.get((evidence_type or "").upper())
    if label is None:
        _log.warning(
            "temporal: unknown evidence_type=%r for evidence node — skipped",
            evidence_type,
        )
        return None
    props: dict = {"lumogis_id": evidence_id, "user_id": user_id, "scope": PERSONAL_SCOPE}
    if label == EVIDENCE_TYPE_LABEL_MAP["DOCUMENT"]:
        props["file_path"] = evidence_id
    return gs.create_node(labels=[label], properties=props)


def record_supersedes(
    gs,
    *,
    superseding_evidence_id: str,
    superseding_evidence_type: str,
    superseded_evidence_id: str,
    superseded_evidence_type: str,
    invalidated_edge_uid: str,
    edge_type: str,
    entity_pair: tuple[str, str],
    at: datetime,
    applied: bool,
    confidence: float,
    user_id: str,
    scope: str | None = None,
) -> bool:
    """Create/refresh the SUPERSEDES edge between two evidence nodes.

    ``applied=False`` rows are shadow-mode proposals — the same structure
    records proposals and applied supersessions (in-graph review surface, no
    new Postgres table). ``scope`` tags shared-scope conflict records
    (LUM-104 checkpoint fold 2a: detection + record only, never auto-applied).
    MERGE key is the invalidated edge's uid (via the ``evidence_id`` slot of
    the GraphStore ``create_edge`` port) so a sweep retry cannot duplicate
    the record. Returns False when either evidence node cannot be resolved —
    the caller falls back to invalidated-edge properties only.
    """
    from_id = _merge_evidence_node(gs, superseding_evidence_id, superseding_evidence_type, user_id)
    to_id = _merge_evidence_node(gs, superseded_evidence_id, superseded_evidence_type, user_id)
    if from_id is None or to_id is None:
        return False
    props: dict = {
        # evidence_id doubles as the create_edge MERGE key (idempotent retry).
        "evidence_id": invalidated_edge_uid,
        "invalidated_edge_uid": invalidated_edge_uid,
        "edge_type": edge_type,
        "entity_pair": f"{entity_pair[0]}|{entity_pair[1]}",
        "at": to_graph_ts(at),
        "applied": bool(applied),
        "confidence": float(confidence),
        "user_id": user_id,
    }
    if scope:
        props["scope"] = scope
    gs.create_edge(
        from_id=from_id,
        to_id=to_id,
        rel_type=EdgeType.SUPERSEDES,
        properties=props,
    )
    return True


def record_proposal(gs, **kwargs) -> bool:
    """Record a shadow-mode (``applied: false``) SUPERSEDES proposal."""
    kwargs.pop("applied", None)
    return record_supersedes(gs, applied=False, **kwargs)


# ---------------------------------------------------------------------------
# Evidence-node temporal_checked stamp (the sweep's idempotency marker)
# ---------------------------------------------------------------------------


def stamp_temporal_checked(
    gs,
    *,
    evidence_id: str,
    evidence_type: str,
    user_id: str,
    status: str,
    checked_at: datetime | None = None,
) -> bool:
    """Stamp ``temporal_checked_at`` + ``temporal_check_status`` on the node.

    Absence of the stamp means "pipeline still owes this episode a pass" —
    the hourly sweep keys on it. ``status`` must be one of
    TEMPORAL_CHECK_STATUSES (raises ValueError otherwise, so a typo cannot
    silently stall the sweep contract).
    """
    if status not in TEMPORAL_CHECK_STATUSES:
        raise ValueError(f"temporal_check_status {status!r} not in {TEMPORAL_CHECK_STATUSES}")
    node_id = _merge_evidence_node(gs, evidence_id, evidence_type, user_id)
    if node_id is None:
        return False
    ts = to_graph_ts(checked_at if checked_at is not None else now_utc())
    gs.create_node(
        labels=[EVIDENCE_TYPE_LABEL_MAP[(evidence_type or "").upper()]],
        properties={
            "lumogis_id": evidence_id,
            "user_id": user_id,
            "scope": PERSONAL_SCOPE,
            PROP_TEMPORAL_CHECKED_AT: ts,
            PROP_TEMPORAL_CHECK_STATUS: status,
        },
    )
    return True
