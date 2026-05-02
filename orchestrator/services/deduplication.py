# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Probabilistic entity deduplication — Pass 4b of the KG Quality Pipeline.

Uses Splink with DuckDB as the in-process compute backend.  No separate
service required.

Blocking strategy (a candidate pair must pass at least one):
  1. Type-based      — only compare entities of the same entity_type
  2. Qdrant ANN      — top-10 nearest neighbours per entity (user_id filtered)
  3. Attribute       — entities sharing the first 2 characters of normalised name

Scoring features:
  - Jaro-Winkler similarity on full name
  - Exact match on entity_type
  - Embedding cosine similarity (from Qdrant ANN payload)
  - Shared alias: either entity has an alias matching the other's name (case-insensitive)

Decision thresholds:
  >= 0.85  → auto-merge if both entities have mention_count >= 2
             (single-mention entities too uncertain; sent to review queue instead)
  0.50–0.85 → insert into dedup_candidates + review_queue for human decision
  < 0.50   → ignored

Model persistence:
  Load from SPLINK_MODEL_PATH if the file exists and is readable.
  Train fresh via EM if the file is missing or unreadable.
  Save after training; save failure logs ERROR but does not abort the run.

Interrupted runs (orchestrator killed mid-job):
  Row stays with finished_at IS NULL.  Next scheduled run starts fresh —
  no resume attempt is made.

Public interface:
  run_deduplication_job(user_id) -> dict
    Never raises.  Returns partial summary on exception.
"""

import json
import logging
import os
import time
from pathlib import Path

import config

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_AUTO_MERGE_THRESHOLD = 0.85
_REVIEW_THRESHOLD = 0.50
_MIN_MENTION_COUNT_FOR_AUTO_MERGE = 2
_ANN_TOP_K = 10
_ATTR_PREFIX_LEN = 2

# SPLINK_MODEL_PATH: where the trained Splink model JSON is persisted.
# Defaults to /workspace/splink_model.json inside the container.
_SPLINK_MODEL_PATH = Path(os.environ.get("SPLINK_MODEL_PATH", "/workspace/splink_model.json"))


# ---------------------------------------------------------------------------
# Blocking helpers
# ---------------------------------------------------------------------------


def _normalise_name(name: str) -> str:
    return (name or "").lower().strip()


def _build_type_blocks(entities: list[dict]) -> set[tuple[str, str]]:
    """Return candidate pairs sharing the same entity_type.

    Only entities of the same type are compared.  Pairs are canonical (a < b).
    """
    by_type: dict[str, list[str]] = {}
    for e in entities:
        etype = e.get("entity_type") or ""
        by_type.setdefault(etype, []).append(str(e["entity_id"]))

    pairs: set[tuple[str, str]] = set()
    for eids in by_type.values():
        eids_sorted = sorted(eids)
        for i in range(len(eids_sorted)):
            for j in range(i + 1, len(eids_sorted)):
                pairs.add((eids_sorted[i], eids_sorted[j]))
    return pairs


def _build_attr_blocks(entities: list[dict]) -> set[tuple[str, str]]:
    """Return candidate pairs sharing the first N chars of their normalised name."""
    by_prefix: dict[str, list[str]] = {}
    for e in entities:
        norm = _normalise_name(e.get("name") or "")
        if len(norm) < _ATTR_PREFIX_LEN:
            continue
        prefix = norm[:_ATTR_PREFIX_LEN]
        by_prefix.setdefault(prefix, []).append(str(e["entity_id"]))

    pairs: set[tuple[str, str]] = set()
    for eids in by_prefix.values():
        eids_sorted = sorted(eids)
        for i in range(len(eids_sorted)):
            for j in range(i + 1, len(eids_sorted)):
                pairs.add((eids_sorted[i], eids_sorted[j]))
    return pairs


def _build_ann_blocks(
    entities: list[dict],
    vs,
    user_id: str,
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], float]]:
    """Query Qdrant ANN for each entity and build candidate pairs.

    Returns:
      pairs      — set of canonical (a, b) tuples
      cos_sims   — {(a, b): cosine_similarity} from Qdrant scores
    """
    entity_map = {str(e["entity_id"]): e for e in entities}
    pairs: set[tuple[str, str]] = set()
    cos_sims: dict[tuple[str, str], float] = {}

    # We need embeddings to query ANN.  Retrieve entity vectors from Qdrant payload
    # if stored, or skip ANN for entities without an embedding.
    embedder = config.get_embedder()

    for e in entities:
        eid = str(e["entity_id"])
        name = e.get("name") or ""
        aliases = list(e.get("aliases") or [])
        if not name:
            continue

        # Build a query embedding for this entity
        embed_text = name
        if aliases:
            embed_text += f": {', '.join(aliases[:3])}"
        try:
            vec = embedder.embed(embed_text)
        except Exception:
            _log.debug("dedup ANN: embed failed for entity_id=%s", eid)
            continue

        # Per plan §185: ANN candidates restricted to (user_id=me,
        # scope='personal') so dedup never proposes merging a personal
        # row with a shared/system projection.  The owner-filter still
        # protects against cross-user leakage; the scope filter
        # protects against projection-vs-source false positives.
        try:
            results = vs.search(
                collection="entities",
                vector=vec,
                limit=_ANN_TOP_K,
                threshold=0.0,
                filter={
                    "must": [
                        {"key": "user_id", "match": {"value": user_id}},
                        {"key": "scope", "match": {"value": "personal"}},
                    ]
                },
            )
        except Exception:
            _log.debug("dedup ANN: search failed for entity_id=%s", eid, exc_info=True)
            continue

        for hit in results:
            payload = hit.get("payload") or {}
            neighbour_eid = str(payload.get("entity_id") or "")
            if not neighbour_eid or neighbour_eid == eid:
                continue
            if neighbour_eid not in entity_map:
                continue

            a, b = sorted([eid, neighbour_eid])
            pairs.add((a, b))
            score = float(hit.get("score") or 0.0)
            # Keep highest cosine sim seen for this pair
            existing = cos_sims.get((a, b), 0.0)
            if score > existing:
                cos_sims[(a, b)] = score

    return pairs, cos_sims


def _build_candidates(
    entities: list[dict],
    vs,
    user_id: str,
    known_distinct: set[tuple[str, str]],
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], float]]:
    """Combine all three blockers into a deduplicated candidate set.

    Skips pairs in known_distinct_entity_pairs.
    Returns (candidate_pairs, cos_sims).
    """
    type_pairs = _build_type_blocks(entities)
    attr_pairs = _build_attr_blocks(entities)
    ann_pairs, cos_sims = _build_ann_blocks(entities, vs, user_id)

    all_pairs = type_pairs | attr_pairs | ann_pairs

    # Filter known-distinct pairs
    candidates = all_pairs - known_distinct

    return candidates, cos_sims


# ---------------------------------------------------------------------------
# Scoring helpers (Jaro-Winkler, alias match, cosine)
# ---------------------------------------------------------------------------


def _jaro(s1: str, s2: str) -> float:
    """Jaro similarity."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    match_dist = max(len1, len2) // 2 - 1
    if match_dist < 0:
        match_dist = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_dist)
        end = min(i + match_dist + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    return (matches / len1 + matches / len2 + (matches - transpositions / 2) / matches) / 3.0


def _jaro_winkler(s1: str, s2: str, p: float = 0.1) -> float:
    """Jaro-Winkler similarity (prefix scale p, max prefix length 4)."""
    jaro = _jaro(s1, s2)
    prefix = 0
    for ch1, ch2 in zip(s1[:4], s2[:4]):
        if ch1 == ch2:
            prefix += 1
        else:
            break
    return jaro + prefix * p * (1 - jaro)


def _alias_match(e_a: dict, e_b: dict) -> bool:
    """True if either entity has an alias matching the other's name (case-insensitive)."""
    name_a = (e_a.get("name") or "").lower()
    name_b = (e_b.get("name") or "").lower()
    aliases_a = [a.lower() for a in (e_a.get("aliases") or [])]
    aliases_b = [b.lower() for b in (e_b.get("aliases") or [])]
    return name_b in aliases_a or name_a in aliases_b


def _compute_pair_features(
    e_a: dict,
    e_b: dict,
    cos_sim: float,
) -> dict:
    """Compute feature dict for a candidate pair."""
    name_a = _normalise_name(e_a.get("name") or "")
    name_b = _normalise_name(e_b.get("name") or "")
    jw = _jaro_winkler(name_a, name_b)
    type_match = 1.0 if e_a.get("entity_type") == e_b.get("entity_type") else 0.0
    alias = 1.0 if _alias_match(e_a, e_b) else 0.0

    return {
        "jaro_winkler_name": jw,
        "entity_type_match": type_match,
        "embedding_cosine": cos_sim,
        "alias_match": alias,
    }


# ---------------------------------------------------------------------------
# Splink model load / train / save
# ---------------------------------------------------------------------------


def _load_or_train_splink_model(candidate_rows: list[dict], model_path: Path):
    """Return a trained Splink model.

    Tries to load from model_path.  Falls back to training via EM on candidate_rows.
    candidate_rows: list of dicts with keys unique_id_a, unique_id_b, and feature columns.
    """
    import splink.comparison_library as cl
    from splink import DuckDBAPI
    from splink import Linker
    from splink import SettingsCreator

    db_api = DuckDBAPI()

    comparisons = [
        cl.JaroWinklerAtThresholds("name_a", "name_b").configure(term_frequency_adjustments=False),
        cl.ExactMatch("entity_type"),
        cl.ExactMatch("alias_match"),
    ]

    settings = SettingsCreator(
        link_type="dedupe_only",
        comparisons=comparisons,
        blocking_rules_to_generate_predictions=[],
        retain_intermediate_calculation_columns=False,
    )

    # Attempt to load existing model
    if model_path.is_file():
        try:
            raw = model_path.read_text(encoding="utf-8")
            saved_settings = json.loads(raw)
            linker = Linker(candidate_rows, saved_settings, db_api=db_api)
            _log.info("dedup: Splink model loaded from %s", model_path)
            return linker
        except Exception as exc:
            _log.warning(
                "dedup: failed to load Splink model from %s (%s) — training fresh",
                model_path,
                exc,
            )

    # Train fresh via EM
    if not candidate_rows:
        # No candidates — initialise with empty data so we can still return a linker
        linker = Linker(candidate_rows, settings, db_api=db_api)
        _log.info("dedup: no candidates — Splink linker initialised but EM skipped")
        return linker

    linker = Linker(candidate_rows, settings, db_api=db_api)
    try:
        linker.training.estimate_probability_two_random_records_match(
            [
                "(l.entity_type = r.entity_type)",
            ],
            recall=0.7,
        )
        linker.training.estimate_u_using_random_sampling(max_pairs=1e5)
        linker.training.estimate_parameters_using_expectation_maximisation(
            "(l.entity_type = r.entity_type)",
            estimate_without_term_frequencies=True,
        )
    except Exception as exc:
        _log.warning("dedup: EM training partially failed (%s) — proceeding with model as-is", exc)

    # Persist model
    try:
        model_json = linker.misc.save_model_to_json()
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text(json.dumps(model_json, indent=2), encoding="utf-8")
        _log.info("dedup: Splink model saved to %s", model_path)
    except Exception as exc:
        _log.error("dedup: failed to save Splink model to %s: %s", model_path, exc)

    return linker


def _score_candidates_with_splink(
    candidate_pairs: set[tuple[str, str]],
    entity_map: dict[str, dict],
    cos_sims: dict[tuple[str, str], float],
    model_path: Path,
) -> list[dict]:
    """Score candidate pairs with Splink.

    Returns list of {entity_id_a, entity_id_b, match_probability, features}.
    Returns [] on any fatal error.
    """
    if not candidate_pairs:
        return []

    # Build input records for Splink in dedupe_only format.
    # Splink dedupe_only expects a single table with one row per entity.
    # We feed it candidate features directly via prediction table approach.
    # Since we supply pre-blocked pairs, we use the scored_pairs_table approach.
    records: list[dict] = []
    for a_id, b_id in candidate_pairs:
        e_a = entity_map.get(a_id)
        e_b = entity_map.get(b_id)
        if e_a is None or e_b is None:
            continue
        cos_sim = cos_sims.get((a_id, b_id), 0.0)
        features = _compute_pair_features(e_a, e_b, cos_sim)
        records.append(
            {
                "unique_id_a": a_id,
                "unique_id_b": b_id,
                "entity_id_a": a_id,
                "entity_id_b": b_id,
                "name_a": _normalise_name(e_a.get("name") or ""),
                "name_b": _normalise_name(e_b.get("name") or ""),
                "entity_type_a": e_a.get("entity_type") or "",
                "entity_type_b": e_b.get("entity_type") or "",
                "entity_type": e_a.get("entity_type") or "",
                "alias_match_a": str(int(_alias_match(e_a, e_b))),
                "alias_match_b": str(int(_alias_match(e_a, e_b))),
                "alias_match": str(int(_alias_match(e_a, e_b))),
                "_features": features,
            }
        )

    if not records:
        return []

    try:
        linker = _load_or_train_splink_model(records, model_path)
        df_pred = linker.inference.predict(threshold_match_probability=0.0)
        results_df = df_pred.as_pandas_dataframe()
    except Exception:
        _log.exception("dedup: Splink predict failed — falling back to feature-weighted scoring")
        return _fallback_score(records)

    scored: list[dict] = []
    for _, row in results_df.iterrows():
        a_id = str(row.get("unique_id_a") or row.get("entity_id_a_l") or "")
        b_id = str(row.get("unique_id_b") or row.get("entity_id_a_r") or "")
        prob = float(row.get("match_probability") or 0.0)

        # Reconstruct canonical ordering
        if a_id > b_id:
            a_id, b_id = b_id, a_id

        feat_rec = next(
            (r for r in records if r["entity_id_a"] == a_id and r["entity_id_b"] == b_id),
            None,
        )
        features = feat_rec["_features"] if feat_rec else {}

        scored.append(
            {
                "entity_id_a": a_id,
                "entity_id_b": b_id,
                "match_probability": prob,
                "features": features,
            }
        )

    return scored


def _fallback_score(records: list[dict]) -> list[dict]:
    """Simple weighted feature scorer used when Splink predict fails."""
    scored = []
    for r in records:
        feats = r.get("_features") or {}
        prob = (
            0.45 * feats.get("jaro_winkler_name", 0.0)
            + 0.25 * feats.get("entity_type_match", 0.0)
            + 0.20 * feats.get("embedding_cosine", 0.0)
            + 0.10 * feats.get("alias_match", 0.0)
        )
        prob = max(0.0, min(1.0, prob))
        scored.append(
            {
                "entity_id_a": r["entity_id_a"],
                "entity_id_b": r["entity_id_b"],
                "match_probability": prob,
                "features": feats,
            }
        )
    return scored


# ---------------------------------------------------------------------------
# Deduplication run lifecycle
# ---------------------------------------------------------------------------


def _insert_run(ms, user_id: str) -> str:
    """Insert a deduplication_runs row and return the run_id."""
    row = ms.fetch_one(
        "INSERT INTO deduplication_runs (user_id) VALUES (%s) RETURNING run_id",
        (user_id,),
    )
    return str(row["run_id"])


def _update_run(
    ms,
    run_id: str,
    *,
    candidate_count: int,
    auto_merged: int,
    queued_for_review: int,
    known_distinct: int,
    error_message: str | None = None,
) -> None:
    ms.execute(
        "UPDATE deduplication_runs SET "
        "  finished_at       = NOW(), "
        "  candidate_count   = %s, "
        "  auto_merged       = %s, "
        "  queued_for_review = %s, "
        "  known_distinct    = %s, "
        "  error_message     = %s "
        "WHERE run_id = %s",
        (candidate_count, auto_merged, queued_for_review, known_distinct, error_message, run_id),
    )


def _update_run_error(ms, run_id: str, error_message: str) -> None:
    try:
        ms.execute(
            "UPDATE deduplication_runs SET finished_at = NOW(), error_message = %s "
            "WHERE run_id = %s",
            (error_message, run_id),
        )
    except Exception:
        _log.exception("dedup: failed to update run error for run_id=%s", run_id)


# ---------------------------------------------------------------------------
# Winner selection
# ---------------------------------------------------------------------------


def _select_winner(e_a: dict, e_b: dict) -> tuple[str, str]:
    """Return (winner_id, loser_id).

    Higher mention_count wins.  Tie-break: lower UUID string (lexicographic).
    """
    mc_a = e_a.get("mention_count") or 0
    mc_b = e_b.get("mention_count") or 0
    id_a = str(e_a["entity_id"])
    id_b = str(e_b["entity_id"])
    if mc_a > mc_b:
        return id_a, id_b
    if mc_b > mc_a:
        return id_b, id_a
    # tie-break: lower UUID string wins
    if id_a <= id_b:
        return id_a, id_b
    return id_b, id_a


# ---------------------------------------------------------------------------
# Route decisions
# ---------------------------------------------------------------------------


def _route_pair(
    ms,
    run_id: str,
    user_id: str,
    e_a: dict,
    e_b: dict,
    prob: float,
    features: dict,
) -> str:
    """Apply threshold routing for one scored pair.

    Returns 'auto_merged', 'queued', or 'ignored'.
    """
    a_id = str(e_a["entity_id"])
    b_id = str(e_b["entity_id"])

    if prob >= _AUTO_MERGE_THRESHOLD:
        mc_a = e_a.get("mention_count") or 0
        mc_b = e_b.get("mention_count") or 0
        if mc_a >= _MIN_MENTION_COUNT_FOR_AUTO_MERGE and mc_b >= _MIN_MENTION_COUNT_FOR_AUTO_MERGE:
            winner_id, loser_id = _select_winner(e_a, e_b)
            try:
                from services.entity_merge import merge_entities

                merge_entities(winner_id=winner_id, loser_id=loser_id, user_id=user_id)
                _log.info(
                    "dedup: auto-merged winner=%s loser=%s prob=%.4f",
                    winner_id,
                    loser_id,
                    prob,
                )
                return "auto_merged"
            except Exception:
                _log.exception(
                    "dedup: auto-merge failed winner=%s loser=%s — routing to review queue",
                    winner_id,
                    loser_id,
                )
                # Fall through to queue

        # mention_count guard failed or merge failed → queue instead
        _insert_candidate_and_queue(ms, run_id, user_id, a_id, b_id, prob, features)
        return "queued"

    if prob >= _REVIEW_THRESHOLD:
        _insert_candidate_and_queue(ms, run_id, user_id, a_id, b_id, prob, features)
        return "queued"

    return "ignored"


def _insert_candidate_and_queue(
    ms,
    run_id: str,
    user_id: str,
    a_id: str,
    b_id: str,
    prob: float,
    features: dict,
) -> None:
    """Insert into dedup_candidates and review_queue."""
    # Canonical ordering: a < b
    if a_id > b_id:
        a_id, b_id = b_id, a_id

    try:
        ms.execute(
            "INSERT INTO dedup_candidates "
            "(run_id, user_id, entity_id_a, entity_id_b, match_probability, features) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            (run_id, user_id, a_id, b_id, prob, json.dumps(features)),
        )
    except Exception:
        _log.exception("dedup: failed to insert dedup_candidate a=%s b=%s", a_id, b_id)

    try:
        ms.execute(
            "INSERT INTO review_queue (candidate_a_id, candidate_b_id, reason, user_id) "
            "VALUES (%s, %s, %s, %s)",
            (
                a_id,
                b_id,
                f"Splink dedup prob={prob:.3f}",
                user_id,
            ),
        )
    except Exception:
        _log.exception("dedup: failed to insert review_queue a=%s b=%s", a_id, b_id)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def run_deduplication_job(user_id: str = "default") -> dict:
    """Run full deduplication pipeline for user.

    Returns {run_id, candidate_count, auto_merged, queued_for_review, duration_ms}.
    Never raises — logs ERROR on exception, updates run row, returns partial summary.
    """
    t0 = time.monotonic()
    run_id: str | None = None
    ms = config.get_metadata_store()
    vs = config.get_vector_store()

    summary: dict = {
        "run_id": None,
        "candidate_count": 0,
        "auto_merged": 0,
        "queued_for_review": 0,
        "duration_ms": 0,
    }

    try:
        run_id = _insert_run(ms, user_id)
        summary["run_id"] = run_id
        _log.info("dedup: run started run_id=%s user_id=%s", run_id, user_id)

        # Fetch all non-staged personal entities for this user.
        # Plan §185 hard constraint: dedup candidates are restricted to
        # `scope='personal'` and per-user.  Shared/system projections
        # (rows where `published_from IS NOT NULL`) are excluded — they
        # are derived rows, not authoritative entities, and merging them
        # would corrupt the projection's link to its personal source.
        # SCOPE-EXEMPT: dedup is personal-scope-only per plan §2.11; the
        # query already narrows with `AND scope = 'personal'`. Shared/system
        # projections are never dedup candidates.
        entity_rows = ms.fetch_all(
            "SELECT entity_id, name, entity_type, aliases, mention_count "
            "FROM entities "
            "WHERE user_id = %s AND is_staged = FALSE AND scope = 'personal'",
            (user_id,),
        )
        entity_map: dict[str, dict] = {str(r["entity_id"]): r for r in entity_rows}

        # SCOPE-EXEMPT: `known_distinct_entity_pairs` is in plan §2.10's
        # excluded-from-scope list (no `scope` column); per-user filtering
        # is the correct visibility model for this table.
        distinct_rows = ms.fetch_all(
            "SELECT entity_id_a, entity_id_b FROM known_distinct_entity_pairs WHERE user_id = %s",
            (user_id,),
        )
        known_distinct: set[tuple[str, str]] = set()
        for dr in distinct_rows:
            known_distinct.add((str(dr["entity_id_a"]), str(dr["entity_id_b"])))

        _log.info(
            "dedup: entities=%d known_distinct=%d",
            len(entity_map),
            len(known_distinct),
        )

        # Blocking
        entities = list(entity_map.values())
        candidates, cos_sims = _build_candidates(entities, vs, user_id, known_distinct)
        _log.info("dedup: candidate pairs after blocking=%d", len(candidates))

        # Score
        scored = _score_candidates_with_splink(
            candidates,
            entity_map,
            cos_sims,
            _SPLINK_MODEL_PATH,
        )

        # Route decisions
        auto_merged = 0
        queued_for_review = 0
        for item in scored:
            a_id = item["entity_id_a"]
            b_id = item["entity_id_b"]
            e_a = entity_map.get(a_id)
            e_b = entity_map.get(b_id)
            if e_a is None or e_b is None:
                continue
            outcome = _route_pair(
                ms,
                run_id,
                user_id,
                e_a,
                e_b,
                item["match_probability"],
                item.get("features") or {},
            )
            if outcome == "auto_merged":
                auto_merged += 1
            elif outcome == "queued":
                queued_for_review += 1

        _update_run(
            ms,
            run_id,
            candidate_count=len(scored),
            auto_merged=auto_merged,
            queued_for_review=queued_for_review,
            known_distinct=len(known_distinct),
        )

        duration_ms = int((time.monotonic() - t0) * 1000)
        summary.update(
            {
                "candidate_count": len(scored),
                "auto_merged": auto_merged,
                "queued_for_review": queued_for_review,
                "duration_ms": duration_ms,
            }
        )

        _log.info(
            "component=deduplication run_id=%s user_id=%s candidate_count=%d "
            "auto_merged=%d queued_for_review=%d duration_ms=%d",
            run_id,
            user_id,
            len(scored),
            auto_merged,
            queued_for_review,
            duration_ms,
        )

    except Exception as exc:
        error_msg = str(exc)
        _log.error(
            "dedup: run failed run_id=%s user_id=%s: %s",
            run_id,
            user_id,
            error_msg,
            exc_info=True,
        )
        if run_id:
            _update_run_error(ms, run_id, error_msg[:1000])
        summary["duration_ms"] = int((time.monotonic() - t0) * 1000)

    return summary
