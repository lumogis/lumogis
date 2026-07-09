# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""TEMPR recall fusion — the read half of the MCP memory server (LUM-295).

`recall` runs four retrieval legs concurrently over the LUM-291 write surface —
**semantic** (Qdrant), **BM25** (Postgres `tsvector`), **graph** (Postgres
`entity_edges`, 1-hop), and **temporal** (recency among currently-valid rows) —
fuses their ranked id lists with Reciprocal Rank Fusion (RRF), hydrates the
fused ids from Postgres (the temporal validity filter lives here — Qdrant has
no `valid_until`, so this is what makes LUM-526 archive/supersede observable),
and optionally reranks the top candidates with a cross-encoder.

Design notes (see .cursor/plans/LUM-295-tempr-recall-fusion.plan.md):
* **Sync** (every MCP tool is sync); leg parallelism uses a per-call
  ``ThreadPoolExecutor`` (no module-level singleton → no thread leak across
  TestClient lifespan restarts). Each leg is wrapped so any failure/timeout
  degrades that leg to ``[]`` without aborting recall.
* **Hydrate before rerank** so the cross-encoder receives candidate ``text``.
* Every query is parameterised and ``user_id``-scoped; the Qdrant filter uses
  the explicit ``{"must":[{"key":…}]}`` shape (a flat dict silently applies no
  filter in ``QdrantStore._build_filter`` → cross-user leak).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone

from models.recall import RecalledMemory

from services import banks

_log = logging.getLogger(__name__)

COLLECTION = "memories"

_RRF_K = 60
_CANDIDATE_K = 50
_RERANK_FANIN = 20
_SEED_K = 10
_LEG_TIMEOUT_S = 5.0
_MAX_LIMIT = 100

_VALID_STRATEGIES = ("semantic", "bm25", "graph", "temporal")


# ---------------------------------------------------------------------------
# Retrieval legs — each returns an ordered list of memory_ids; each is wrapped
# by the orchestrator so a failure degrades the leg to [] (recall never 500s).
# ---------------------------------------------------------------------------


def _leg_semantic(query: str, *, user_id: str, bank: str, embedder, vs) -> list[str]:
    """Semantic leg: embed → Qdrant search over the ``memories`` collection.

    The filter MUST use the structured ``{"must":[{"key":…,"match":…}]}`` shape;
    a flat ``{"user_id":…}`` dict falls through to ``Filter(must=[])`` in
    ``QdrantStore._build_filter`` = NO filter = cross-user leak. The Qdrant point
    id is ``uuid5("memory::user::memory_id")``; the memory_id lives in the
    payload, so read ``hit["payload"]["memory_id"]`` (NOT ``hit["id"]``).
    Validity is not in the payload — filtered later at hydration.
    """
    vector = embedder.embed(query)
    bank_clauses = banks.qdrant_bank_filter(bank) or []
    hits = vs.search(
        collection=COLLECTION,
        vector=vector,
        limit=_CANDIDATE_K,
        threshold=0.0,
        filter={
            "must": [
                {"key": "user_id", "match": {"value": user_id}},
                *bank_clauses,
            ]
        },
    )
    out: list[str] = []
    for h in hits:
        mid = (h.get("payload") or {}).get("memory_id")
        if mid:
            out.append(str(mid))
    return out


def _leg_bm25(query: str, *, user_id: str, bank: str, as_of, ms) -> list[str]:
    """BM25 keyword leg: Postgres full-text over ``memories.content_tsv``."""
    if banks.is_cross_bank(bank):
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: user_id-scoped read (no god-mode).
            "SELECT id FROM memories "
            "WHERE user_id = %s "
            "AND (valid_until IS NULL OR valid_until >= %s) "
            "AND content_tsv @@ websearch_to_tsquery('english', %s) "
            "ORDER BY ts_rank_cd(content_tsv, websearch_to_tsquery('english', %s)) DESC "
            "LIMIT %s",
            (user_id, as_of, query, query, _CANDIDATE_K),
        )
    else:
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: user_id-scoped read (no god-mode).
            "SELECT id FROM memories "
            "WHERE user_id = %s AND bank = %s "
            "AND (valid_until IS NULL OR valid_until >= %s) "
            "AND content_tsv @@ websearch_to_tsquery('english', %s) "
            "ORDER BY ts_rank_cd(content_tsv, websearch_to_tsquery('english', %s)) DESC "
            "LIMIT %s",
            (user_id, bank, as_of, query, query, _CANDIDATE_K),
        )
    return [str(r["id"]) for r in rows]


def _leg_graph(query: str, *, user_id: str, bank: str, as_of, ms, hops: int = 1) -> list[str]:
    """Graph leg: seed entities from the query → memories touching those edges."""
    from services import entities
    from services import entity_edges

    seed_ids = entities.entity_ids_for_query(query, user_id=user_id, limit=_SEED_K, ms=ms)
    if not seed_ids:
        return []
    return entity_edges.memories_for_entities(
        seed_ids, user_id=user_id, bank=bank, as_of=as_of, hops=hops, ms=ms
    )


def _leg_temporal(*, user_id: str, bank: str, as_of, ms) -> list[str]:
    """Temporal leg: recency prior over currently-valid memories."""
    if banks.is_cross_bank(bank):
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: user_id-scoped read (no god-mode).
            "SELECT id FROM memories "
            "WHERE user_id = %s "
            "AND (valid_until IS NULL OR valid_until >= %s) "
            "ORDER BY valid_from DESC "
            "LIMIT %s",
            (user_id, as_of, _CANDIDATE_K),
        )
    else:
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: user_id-scoped read (no god-mode).
            "SELECT id FROM memories "
            "WHERE user_id = %s AND bank = %s "
            "AND (valid_until IS NULL OR valid_until >= %s) "
            "ORDER BY valid_from DESC "
            "LIMIT %s",
            (user_id, bank, as_of, _CANDIDATE_K),
        )
    return [str(r["id"]) for r in rows]


# ---------------------------------------------------------------------------
# Fusion + hydration
# ---------------------------------------------------------------------------


def _rrf(ranked_lists: dict[str, list[str]], k: int = _RRF_K) -> list[tuple[str, float, list[str]]]:
    """Reciprocal Rank Fusion over rank position (score-agnostic).

    ``score(id) = Σ_legs 1/(k + rank)`` (rank 0-based). Returns
    ``(memory_id, score, source_strategies)`` sorted by score desc, then by id
    for deterministic ordering. ``source_strategies`` lists the legs that
    surfaced the id, in a stable strategy order.
    """
    scores: dict[str, float] = {}
    sources: dict[str, list[str]] = {}
    for strategy in _VALID_STRATEGIES:
        ids = ranked_lists.get(strategy)
        if not ids:
            continue
        for rank, mid in enumerate(ids):
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank)
            sources.setdefault(mid, []).append(strategy)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(mid, score, sources[mid]) for mid, score in ordered]


def _hydrate(ids: list[str], *, user_id: str, bank: str, as_of, ms) -> dict[str, dict]:
    """Hydrate fused ids from Postgres, applying the temporal validity filter.

    Two parameterised queries: (1) the memory rows (only those valid at
    ``as_of`` survive — archived ids are dropped here, which is what makes
    LUM-526 observable); (2) the ``entity_edges`` union/GROUP BY to populate
    ``entity_ids`` per memory. Returns ``{memory_id: {content, entity_ids,
    valid_from, valid_until}}`` for the surviving ids. When ``bank`` is not
    cross-bank, rows whose ``bank`` column differs are dropped (defense in depth).
    """
    if not ids:
        return {}

    rows = ms.fetch_all(
        # SCOPE-EXEMPT: user_id-scoped read (no god-mode).
        "SELECT id, content, bank, valid_from, valid_until "
        "FROM memories "
        "WHERE id = ANY(%s) AND user_id = %s "
        "AND (valid_until IS NULL OR valid_until >= %s)",
        (ids, user_id, as_of),
    )
    hydrated: dict[str, dict] = {}
    for r in rows:
        row_bank = str(r["bank"])
        if not banks.is_cross_bank(bank) and row_bank != bank:
            continue
        hydrated[str(r["id"])] = {
            "id": str(r["id"]),
            "content": r["content"],
            "bank": row_bank,
            "entity_ids": [],
            "valid_from": r["valid_from"],
            "valid_until": r.get("valid_until"),
        }

    if hydrated:
        edge_rows = ms.fetch_all(
            # SCOPE-EXEMPT: user_id-scoped read (no god-mode).
            "SELECT evidence_id, ARRAY_AGG(DISTINCT eid) AS entity_ids FROM ("
            "  SELECT evidence_id, unnest(ARRAY[src_entity_id, dst_entity_id]) AS eid "
            "  FROM entity_edges "
            "  WHERE evidence_id = ANY(%s) AND user_id = %s "
            "  AND (valid_until IS NULL OR valid_until >= %s)"
            ") s GROUP BY evidence_id",
            (list(hydrated.keys()), user_id, as_of),
        )
        for er in edge_rows:
            mid = str(er["evidence_id"])
            if mid in hydrated:
                hydrated[mid]["entity_ids"] = [str(e) for e in (er.get("entity_ids") or []) if e]

    return hydrated


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _run_leg(fn) -> list[str]:
    """Wrap a leg thunk so any exception degrades it to ``[]`` (logged)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — one leg failing must not fail recall.
        _log.warning("recall leg failed (degraded to []): %s", exc)
        return []


def recall(
    *,
    user_id: str,
    bank: str = "coding",
    query: str,
    limit: int = 10,
    retrieval_strategies: list[str] | tuple[str, ...] = _VALID_STRATEGIES,
    as_of: datetime | None = None,
    rerank: bool = True,
    ms=None,
    embedder=None,
    vs=None,
    reranker=None,
) -> list[RecalledMemory]:
    """Fused memory recall (LUM-295). Sync; legs run in a per-call thread pool.

    See module docstring. Returns ``[]`` on an empty/whitespace query or when no
    leg surfaces a currently-valid memory.
    """
    if not query or not query.strip():
        return []
    try:
        bank = banks.validate_bank_for_recall(bank)
    except ValueError:
        _log.warning("recall: invalid bank %r", bank)
        return []
    limit = max(1, min(int(limit), _MAX_LIMIT))
    as_of = as_of or datetime.now(timezone.utc)

    import config  # lazy — avoids the adapter/credential import chain at module load

    ms = ms or config.get_metadata_store()
    embedder = embedder if embedder is not None else config.get_embedder()
    vs = vs if vs is not None else config.get_vector_store()

    requested = [s for s in retrieval_strategies if s in _VALID_STRATEGIES]
    for s in retrieval_strategies:
        if s not in _VALID_STRATEGIES:
            _log.warning("recall: ignoring unknown retrieval strategy %r", s)
    if not requested:
        requested = list(_VALID_STRATEGIES)

    leg_thunks = {
        "semantic": lambda: _leg_semantic(
            query, user_id=user_id, bank=bank, embedder=embedder, vs=vs
        ),
        "bm25": lambda: _leg_bm25(query, user_id=user_id, bank=bank, as_of=as_of, ms=ms),
        "graph": lambda: _leg_graph(query, user_id=user_id, bank=bank, as_of=as_of, ms=ms),
        "temporal": lambda: _leg_temporal(user_id=user_id, bank=bank, as_of=as_of, ms=ms),
    }
    active = {s: leg_thunks[s] for s in requested}

    ranked: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="recall-leg") as ex:
        futures = {s: ex.submit(_run_leg, thunk) for s, thunk in active.items()}
        for s, fut in futures.items():
            try:
                ranked[s] = fut.result(timeout=_LEG_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 — timeout / leg crash → [] for that leg.
                _log.warning("recall leg %s timed out or failed: %s", s, exc)
                ranked[s] = []

    fused = _rrf(ranked)
    if not fused:
        return []

    fused_ids = [mid for mid, _score, _src in fused]
    hydrated = _hydrate(fused_ids, user_id=user_id, bank=bank, as_of=as_of, ms=ms)

    # Keep fused order; drop ids that didn't survive temporal hydration.
    results: list[tuple[str, float, list[str], dict]] = [
        (mid, score, src, hydrated[mid]) for mid, score, src in fused if mid in hydrated
    ]
    if not results:
        return []

    if rerank:
        reranker = reranker if reranker is not None else config.get_recall_reranker()
        if reranker is not None:
            fanin = results[:_RERANK_FANIN]
            candidates = [
                {"text": row["content"], "memory_id": mid} for mid, _s, _src, row in fanin
            ]
            try:
                reranked = reranker.rerank(query, candidates, limit)
                by_id = {mid: (score, src, row) for mid, score, src, row in results}
                ordered = []
                for c in reranked:
                    mid = c.get("memory_id")
                    if mid in by_id:
                        score, src, row = by_id[mid]
                        ordered.append((mid, score, src, row))
                results = ordered
            except Exception as exc:  # noqa: BLE001 — rerank failure → keep RRF order.
                _log.warning("recall rerank failed (kept RRF order): %s", exc)

    out: list[RecalledMemory] = []
    for mid, score, src, row in results[:limit]:
        out.append(
            RecalledMemory(
                id=mid,
                content=row["content"],
                entity_ids=row["entity_ids"],
                valid_from=row["valid_from"],
                valid_until=row.get("valid_until"),
                score=score,
                source_strategies=src,
            )
        )
    return out
