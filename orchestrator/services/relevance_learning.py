# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Feedback → relevance learning (LUM-649).

Wires the existing weighted-sum relevance engine (``signal_processor.match_relevance``)
and the existing generic feedback pipe (``POST /feedback`` → ``feedback_log``) together
via small, bounded, per-user learned weight deltas — never by mutating the shared
``relevance_score``/``importance_score`` columns (those are ingest-time, owner-computed
snapshots copied verbatim onto shared household projections; see ``services/projection.py``).

Two distinct consumers, not to be conflated:
  1. Ingest-time, owner-scoped: ``compute_ingest_learned_component`` feeds an additive
     sixth term into ``match_relevance``, using the signal owner's own learned weights.
  2. Read-time, per-viewer: ``compute_learned_relevance_delta`` feeds a per-caller
     ``learned_relevance_delta`` field on ``GET /signals``, added to ``importance_score``
     for lane placement — never touching stored/shared columns.

Invoked directly and synchronously from ``routes/signals.py::record_feedback`` — not a
hook listener. ``hooks.fire_background`` is asynchronous, so a hook-based design could
never reliably report which term moved within the same HTTP response the transparency
requirement demands (the existing ``Event.FEEDBACK_RECEIVED`` fire is untouched and
still reaches only the proprietary graph plugin's ``item_type="entity"`` listener).
"""

import json
import logging
from typing import Iterable

from auth import UserContext
from visibility import visible_filter

import config

_log = logging.getLogger(__name__)

FEEDBACK_DELTA = 0.15
WEIGHT_MIN = -1.0
WEIGHT_MAX = 1.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _avg_matching_weight(weight_map: dict[str, float], terms: Iterable[str]) -> float:
    """Mean of ``weight_map[t]`` over only the ``t in terms`` that are keys in ``weight_map``.

    Unlearned co-occurring terms are excluded from the average, not treated as implicit
    zeros — otherwise a learned weight on one term would be diluted toward zero by every
    other, never-fed-back-on term that happens to co-occur on the same signal. Returns
    ``0.0`` when ``terms`` is empty or nothing in ``terms`` is a key in ``weight_map``
    (this is what keeps an all-empty weight map — every profile before its first
    feedback — a strict no-op).
    """
    matched = [weight_map[t] for t in terms if t in weight_map]
    if not matched:
        return 0.0
    return sum(matched) / len(matched)


def _signal_learning_terms(row: dict) -> dict:
    """The one place "what counts as a learnable term for this signal" is defined.

    ``row`` is a plain ``{"topics", "entities", "from_addr", "category"}`` dict — a
    ``signals`` row, the dict ``GET /signals`` already builds, or a small adapter dict
    built from a ``Signal`` dataclass at the ingest call site. Every term is
    ``.strip().lower()``-normalized, matching ``match_relevance``/``_fraction_match``'s
    existing lowercasing convention elsewhere in this codebase — otherwise learned
    weights would fragment across LLM-output casing variance ("Acme Corp" vs "acme corp").
    """
    topics = [t.strip().lower() for t in (row.get("topics") or [])]
    entities = [e["name"].strip().lower() for e in (row.get("entities") or []) if e.get("name")]
    from_addr = (row.get("from_addr") or "").strip().lower()
    if from_addr:
        entities.append(from_addr)
    category = (row.get("category") or "").strip().lower()
    categories = [category] if category else []
    return {"topics": topics, "entities": entities, "categories": categories}


def _load_signal_row(*, signal_id: str, user: UserContext) -> dict | None:
    """Load a signal's learning-relevant fields, scoped to the caller's visibility.

    Uses the real ``visibility.visible_filter()`` — the same function and binding
    order ``_signal_visible_to`` already uses (``routes/signals.py``) — rather than a
    hand-rolled predicate, so a caller submitting a ``signal_id`` for a signal they
    cannot see via ``GET /signals`` (another user's personal signal, or a shared
    signal while ``allows_shared=False``) gets ``None``, identical to a nonexistent
    signal_id. Returns ``None`` on a missing/invisible row or DB error (logs a
    warning, never raises).
    """
    try:
        where_clause, where_params = visible_filter(user)
        ms = config.get_metadata_store()
        row = ms.fetch_one(
            f"SELECT topics, entities, from_addr, category FROM signals "
            f"WHERE signal_id = %s AND {where_clause}",
            (signal_id, *where_params),
        )
        return dict(row) if row else None
    except Exception as exc:
        _log.warning("Could not load signal %s for relevance learning: %s", signal_id, exc)
        return None


def load_profile_weights(user_id: str) -> dict:
    """Load a user's learned weight maps as ``{"topics", "entities", "categories"}``.

    Returns all-empty maps (never writes, never raises) when no ``relevance_profiles``
    row exists yet, or on any DB error — a personalization-nudge fetch failing must
    never take down its caller (e.g. the entire ``GET /signals`` response). No leading
    underscore: this is a legitimate module public API called both internally
    (``apply_signal_feedback``) and cross-module from ``routes/signals.py::list_signals``.
    """
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: `relevance_profiles` is per-user signal-routing config
        # (plan §2.10 excluded-from-scope), not memory content; no `scope`
        # column exists — the caller's own `user_id` is the correct filter.
        row = ms.fetch_one(
            "SELECT learned_topic_weights, learned_entity_weights, "
            "learned_category_weights FROM relevance_profiles WHERE user_id = %s",
            (user_id,),
        )
        if not row:
            return {"topics": {}, "entities": {}, "categories": {}}
        return {
            "topics": row.get("learned_topic_weights") or {},
            "entities": row.get("learned_entity_weights") or {},
            "categories": row.get("learned_category_weights") or {},
        }
    except Exception as exc:
        _log.warning("Could not load learned weights for %s: %s", user_id, exc)
        return {"topics": {}, "entities": {}, "categories": {}}


def apply_signal_feedback(*, user: UserContext, signal_id: str, positive: bool) -> dict:
    """Nudge the caller's learned weights from explicit 👍/👎 on a signal.

    Sole, complete guarantor of the never-raises contract: wraps its own signal-load,
    profile-load, and UPSERT internally and returns ``{"demoted": [], "promoted": []}``
    (both empty) on any failure (missing/invisible signal, DB error) — the caller
    (``record_feedback``) relies on this and does not need its own try/except.

    Repeated feedback is intentionally non-idempotent — clicking 👎 twice on similar
    content nudges the weight twice (bounded by the clamp). Feedback is a repeatable
    signal, unlike triage state (a single durable status).
    """
    user_id = user.user_id  # relevance_profiles remains keyed by bare user_id
    try:
        row = _load_signal_row(signal_id=signal_id, user=user)
        if row is None:
            return {"demoted": [], "promoted": []}

        terms = _signal_learning_terms(row)
        weights = load_profile_weights(user_id)
        delta = FEEDBACK_DELTA if positive else -FEEDBACK_DELTA
        touched: list[str] = []

        for axis in ("topics", "entities", "categories"):
            weight_map = weights[axis]
            for term in terms[axis]:
                weight_map[term] = _clamp(weight_map.get(term, 0.0) + delta, WEIGHT_MIN, WEIGHT_MAX)
                touched.append(term)

        # Dedupe: a term appearing in both topics and entities is named once.
        touched = list(dict.fromkeys(touched))

        ms = config.get_metadata_store()
        ms.execute(
            "INSERT INTO relevance_profiles "
            "(user_id, learned_topic_weights, learned_entity_weights, "
            "learned_category_weights, updated_at) "
            "VALUES (%s, %s::jsonb, %s::jsonb, %s::jsonb, NOW()) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "learned_topic_weights = EXCLUDED.learned_topic_weights, "
            "learned_entity_weights = EXCLUDED.learned_entity_weights, "
            "learned_category_weights = EXCLUDED.learned_category_weights, "
            "updated_at = NOW()",
            (
                user_id,
                json.dumps(weights["topics"]),
                json.dumps(weights["entities"]),
                json.dumps(weights["categories"]),
            ),
        )

        if positive:
            return {"demoted": [], "promoted": touched}
        return {"demoted": touched, "promoted": []}
    except Exception as exc:
        _log.warning("apply_signal_feedback failed for %s/%s: %s", user_id, signal_id, exc)
        return {"demoted": [], "promoted": []}


def compute_learned_relevance_delta(
    profile_weights: dict | None,
    *,
    topics: list[str],
    entities: list[dict],
    from_addr: str,
    category: str,
) -> float:
    """Read-time, per-viewer delta for ``GET /signals``'s ``learned_relevance_delta``.

    Pure, per-request, per-caller computation using only the caller's own
    ``relevance_profiles`` row — never reads or writes ``relevance_score``/
    ``importance_score`` in Postgres, so one household member's feedback can never
    change what another member's response contains. Clamped to ±0.6 — a deliberate
    damping choice equal to ``NEEDS_YOU_IMPORTANCE`` itself: feedback can tip a
    borderline signal across the lane threshold. Because the cap equals the threshold
    and the lane test is inclusive (``importance + delta >= 0.6``), a *maximally*
    learned profile (delta = +0.6) lifts an importance-0.0 signal to exactly the
    needs-you threshold — strong, sustained feedback for a topic is itself treated as
    needs-you-worthy — but nothing weaker can, and no downvote buries a 1.0 signal.
    """
    if profile_weights is None:
        return 0.0
    # LUM-671: skip all term extraction + averaging when nothing has been learned
    # (the overwhelmingly common case at launch) — this runs on every GET /signals row.
    if not (
        profile_weights["topics"] or profile_weights["entities"] or profile_weights["categories"]
    ):
        return 0.0
    try:
        terms = _signal_learning_terms(
            {"topics": topics, "entities": entities, "from_addr": from_addr, "category": category}
        )
        return _clamp(
            0.30 * _avg_matching_weight(profile_weights["topics"], terms["topics"])
            + 0.20 * _avg_matching_weight(profile_weights["entities"], terms["entities"])
            + 0.20 * _avg_matching_weight(profile_weights["categories"], terms["categories"]),
            -0.6,
            0.6,
        )
    except Exception as exc:
        # LUM-670: a single malformed signal row (non-dict entity, non-string term)
        # must degrade its own delta to 0.0 — never 500 the whole GET /signals
        # response. Mirrors this module's other never-raises helpers.
        _log.warning("compute_learned_relevance_delta failed (delta→0.0): %s", exc)
        return 0.0


def compute_ingest_learned_component(profile, signal) -> float:
    """Ingest-time, owner-scoped additive term consulted inside ``match_relevance``.

    Reads the signal owner's own learned weights, already loaded onto ``profile`` by
    the existing ``_load_profile`` call in ``process_signal`` — no new DB round-trip.
    Clamped to ±0.3 — a deliberate damping choice: a profile that has downvoted every
    topic/entity/category on a signal maxes out at a 0.3-point pull, comparable in
    scale to one full weighted-term's worth of influence in the original 5-term
    formula, not a dominating swing. Evaluates to exactly ``0.0`` for an all-empty
    learned-weight profile (every profile before this chunk, and before its first
    feedback) — the invariant ``match_relevance``'s backward-compat test proves.
    """
    # LUM-671: skip all work for a profile that has never been fed back on (every
    # profile before this chunk, and before its first feedback) — this runs at
    # every ingest. Preserves the exact 0.0 backward-compat invariant.
    if not (
        profile.learned_topic_weights
        or profile.learned_entity_weights
        or profile.learned_category_weights
    ):
        return 0.0
    try:
        terms = _signal_learning_terms(
            {
                "topics": signal.topics,
                "entities": signal.entities,
                "from_addr": signal.from_addr,
                "category": signal.category,
            }
        )
        return _clamp(
            0.5 * _avg_matching_weight(profile.learned_topic_weights, terms["topics"])
            + 0.3 * _avg_matching_weight(profile.learned_entity_weights, terms["entities"])
            + 0.2 * _avg_matching_weight(profile.learned_category_weights, terms["categories"]),
            -0.3,
            0.3,
        )
    except Exception as exc:
        # LUM-670: a malformed signal must never crash the ingest poll — degrade
        # the learned component to 0.0 (relevance_score falls back to the 5-term sum).
        _log.warning("compute_ingest_learned_component failed (component→0.0): %s", exc)
        return 0.0


def fetch_learned_weights_view(user_id: str) -> dict:
    """Human-readable current learned weights for ``GET /profile/learned``.

    Terms with weight exactly 0.0 (never touched) are omitted — sparse by
    construction, only surfaces what has actually been tuned. Within each axis,
    demoted (negative-weight) entries sort ascending (most-demoted first) and
    promoted (positive-weight) entries sort descending (most-promoted first), so
    the settings view's Demoted/Promoted split needs no further client-side sorting.
    """
    weights = load_profile_weights(user_id)
    result = {}
    for axis in ("topics", "entities", "categories"):
        weight_map = weights[axis]
        demoted = sorted(
            ({"term": t, "weight": w} for t, w in weight_map.items() if w < 0),
            key=lambda item: item["weight"],
        )
        promoted = sorted(
            ({"term": t, "weight": w} for t, w in weight_map.items() if w > 0),
            key=lambda item: item["weight"],
            reverse=True,
        )
        result[axis] = demoted + promoted
    return result
