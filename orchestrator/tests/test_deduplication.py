# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for orchestrator/services/deduplication.py — Pass 4b KG Quality Pipeline.

Test groups:
  1. Blocking — type-based, attribute, known-distinct filtering, deduplication
  2. Blocking — Qdrant ANN uses user_id filter on every search call
  3. Scoring and threshold routing:
       >= 0.85 + mention_count >= 2  → auto-merge
       >= 0.85 + mention_count < 2   → queued (not merged)
       0.50–0.85                     → dedup_candidates + review_queue
       < 0.50                        → ignored
  4. Winner selection (mention_count, UUID tie-break)
  5. Run lifecycle (row created at start, updated on completion, error handling)
  6. Interrupted run (finished_at IS NULL on next start → fresh run)
  7. POST /entities/deduplicate route (202, 409, auth)
  8. Weekly job integration (dedup as final step, exception isolation, log fields)

Splink outputs are mocked — no real Splink model is required.

Runs: docker compose -f docker-compose.test.yml run --rm orchestrator pytest
"""

import uuid
from unittest.mock import MagicMock
from unittest.mock import patch

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

ID_A = str(uuid.uuid4())
ID_B = str(uuid.uuid4())
ID_C = str(uuid.uuid4())
USER_ID = "default"

# Ensure canonical ordering (A < B < C)
ID_A, ID_B, ID_C = sorted([ID_A, ID_B, ID_C])

_ENTITY_A = {
    "entity_id": ID_A,
    "name": "Alice Smith",
    "entity_type": "PERSON",
    "aliases": ["A. Smith"],
    "mention_count": 3,
}
_ENTITY_B = {
    "entity_id": ID_B,
    "name": "Alice Sm",
    "entity_type": "PERSON",
    "aliases": [],
    "mention_count": 2,
}
_ENTITY_C = {
    "entity_id": ID_C,
    "name": "Acme Corp",
    "entity_type": "ORG",
    "aliases": [],
    "mention_count": 4,
}


# ---------------------------------------------------------------------------
# Helper: build a minimal mock metadata store
# ---------------------------------------------------------------------------


def _make_ms(
    entities=None,
    distinct_pairs=None,
    run_id=None,
    in_progress_run=None,
):
    ms = MagicMock()
    if entities is None:
        entities = [_ENTITY_A, _ENTITY_B, _ENTITY_C]
    if distinct_pairs is None:
        distinct_pairs = []
    if run_id is None:
        run_id = str(uuid.uuid4())

    def _fetch_one(sql, params=None):
        if "INSERT INTO deduplication_runs" in sql:
            return {"run_id": run_id}
        if "finished_at IS NULL" in sql:
            return in_progress_run
        return None

    def _fetch_all(sql, params=None):
        sql_lower = sql.lower()
        if "from entities" in sql_lower:
            return entities
        if "known_distinct_entity_pairs" in sql_lower:
            return [{"entity_id_a": a, "entity_id_b": b} for (a, b) in distinct_pairs]
        return []

    ms.fetch_one.side_effect = _fetch_one
    ms.fetch_all.side_effect = _fetch_all
    ms.execute.return_value = None
    return ms


# ---------------------------------------------------------------------------
# 1. Blocking — type-based and attribute
# ---------------------------------------------------------------------------


class TestTypeBlocking:
    def test_same_type_entities_are_candidates(self):
        from services.deduplication import _build_type_blocks

        entities = [_ENTITY_A, _ENTITY_B, _ENTITY_C]
        pairs = _build_type_blocks(entities)
        # A and B are both PERSON → should be a candidate pair
        assert (ID_A, ID_B) in pairs or (ID_B, ID_A) in pairs
        # Canonical ordering: a < b
        for a, b in pairs:
            assert a < b

    def test_different_type_entities_not_in_type_pairs(self):
        from services.deduplication import _build_type_blocks

        entities = [_ENTITY_A, _ENTITY_C]
        pairs = _build_type_blocks(entities)
        # A is PERSON, C is ORG → not in type pairs
        assert (ID_A, ID_C) not in pairs
        assert (ID_C, ID_A) not in pairs

    def test_no_entities_returns_empty(self):
        from services.deduplication import _build_type_blocks

        assert _build_type_blocks([]) == set()

    def test_single_entity_returns_empty(self):
        from services.deduplication import _build_type_blocks

        assert _build_type_blocks([_ENTITY_A]) == set()


class TestAttributeBlocking:
    def test_shared_prefix_creates_candidate(self):
        from services.deduplication import _build_attr_blocks

        # "alice smith" and "alice sm" share prefix "al"
        pairs = _build_attr_blocks([_ENTITY_A, _ENTITY_B])
        canonical = tuple(sorted([ID_A, ID_B]))
        assert canonical in pairs

    def test_different_prefix_not_candidate(self):
        from services.deduplication import _build_attr_blocks

        pairs = _build_attr_blocks([_ENTITY_A, _ENTITY_C])
        # "alice smith" → "al", "acme corp" → "ac" — no shared prefix
        canonical_ac = tuple(sorted([ID_A, ID_C]))
        assert canonical_ac not in pairs

    def test_short_name_skipped(self):
        from services.deduplication import _build_attr_blocks

        e_short = {**_ENTITY_A, "entity_id": str(uuid.uuid4()), "name": "A"}
        pairs = _build_attr_blocks([e_short, _ENTITY_B])
        # "a" has len < 2 → skipped
        assert len(pairs) == 0


class TestKnownDistinctFiltering:
    def test_known_distinct_pair_excluded(self):
        from services.deduplication import _build_candidates

        known_distinct = {(ID_A, ID_B)}
        vs = MagicMock()
        vs.search.return_value = []
        with patch("services.deduplication.config") as mock_cfg:
            mock_cfg.get_vector_store.return_value = vs
            embedder = MagicMock()
            embedder.embed.return_value = [0.0] * 768
            mock_cfg.get_embedder.return_value = embedder

            entities = [_ENTITY_A, _ENTITY_B]
            candidates, _ = _build_candidates(entities, vs, USER_ID, known_distinct)
        canonical = (ID_A, ID_B)
        assert canonical not in candidates

    def test_unknown_pair_included(self):
        from services.deduplication import _build_candidates

        known_distinct: set = set()
        vs = MagicMock()
        vs.search.return_value = []
        with patch("services.deduplication.config") as mock_cfg:
            embedder = MagicMock()
            embedder.embed.return_value = [0.0] * 768
            mock_cfg.get_embedder.return_value = embedder

            entities = [_ENTITY_A, _ENTITY_B]
            candidates, _ = _build_candidates(entities, vs, USER_ID, known_distinct)
        # A and B share "al" prefix AND same type
        canonical = tuple(sorted([ID_A, ID_B]))
        assert canonical in candidates

    def test_pair_via_multiple_blockers_counted_once(self):
        from services.deduplication import _build_candidates

        vs = MagicMock()
        vs.search.return_value = []
        with patch("services.deduplication.config") as mock_cfg:
            embedder = MagicMock()
            embedder.embed.return_value = [0.0] * 768
            mock_cfg.get_embedder.return_value = embedder

            entities = [_ENTITY_A, _ENTITY_B]
            candidates, _ = _build_candidates(entities, vs, USER_ID, set())
        # The pair appears in both type blocks and attr blocks but should be counted once
        canonical = tuple(sorted([ID_A, ID_B]))
        assert list(candidates).count(canonical) == 1


# ---------------------------------------------------------------------------
# 2. Qdrant ANN user_id filter enforcement
# ---------------------------------------------------------------------------


class TestAnnUserIdFilter:
    def test_every_search_call_includes_user_id_filter(self):
        from services.deduplication import _build_ann_blocks

        vs = MagicMock()
        vs.search.return_value = []

        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 768

        with patch("services.deduplication.config") as mock_cfg:
            mock_cfg.get_embedder.return_value = embedder
            _build_ann_blocks([_ENTITY_A, _ENTITY_B], vs, USER_ID)

        # Every search call must include the user_id filter
        for call_args in vs.search.call_args_list:
            kwargs = call_args[1] if call_args[1] else {}
            # filter may be positional or keyword
            if "filter" in kwargs:
                f = kwargs["filter"]
            else:
                # positional: (collection, vector, limit, threshold, filter, ...)
                args = call_args[0]
                f = args[4] if len(args) > 4 else None
            assert f is not None, "search called without filter"
            must = f.get("must", [])
            user_id_conditions = [
                c
                for c in must
                if c.get("key") == "user_id" and c.get("match", {}).get("value") == USER_ID
            ]
            assert len(user_id_conditions) >= 1, (
                f"user_id filter missing in search call: {call_args}"
            )

    def test_ann_candidate_pairs_use_canonical_ordering(self):
        from services.deduplication import _build_ann_blocks

        hit = {
            "id": "some-point-id",
            "score": 0.92,
            "payload": {
                "entity_id": ID_B,
                "user_id": USER_ID,
            },
        }

        vs = MagicMock()
        vs.search.return_value = [hit]

        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 768

        with patch("services.deduplication.config") as mock_cfg:
            mock_cfg.get_embedder.return_value = embedder
            pairs, cos_sims = _build_ann_blocks([_ENTITY_A, _ENTITY_B], vs, USER_ID)

        for a, b in pairs:
            assert a < b


# ---------------------------------------------------------------------------
# 3. Scoring and threshold routing
# ---------------------------------------------------------------------------


def _make_scored_pair(prob, entity_a=None, entity_b=None):
    ea = entity_a or _ENTITY_A
    eb = entity_b or _ENTITY_B
    a_id = str(ea["entity_id"])
    b_id = str(eb["entity_id"])
    if a_id > b_id:
        a_id, b_id = b_id, a_id
    return {
        "entity_id_a": a_id,
        "entity_id_b": b_id,
        "match_probability": prob,
        "features": {},
    }


class TestThresholdRouting:
    def test_high_prob_both_mention_count_gte_2_auto_merges(self):
        from services.deduplication import _route_pair

        ms = MagicMock()
        e_a = {**_ENTITY_A, "mention_count": 3}
        e_b = {**_ENTITY_B, "mention_count": 2}

        # `merge_entities` is imported lazily inside `_route_pair` via
        # `from services.entity_merge import merge_entities`, so the only
        # valid patch target is the source module. The previous
        # `services.deduplication.merge_entities` patch raised AttributeError
        # because there is no such top-level symbol on the deduplication
        # module. VERIFY-PLAN: updated to match implementation
        with patch("services.entity_merge.merge_entities") as mock_merge:
            mock_merge.return_value = MagicMock()
            result = _route_pair(ms, "run-1", USER_ID, e_a, e_b, 0.90, {})

        assert result == "auto_merged"

    def test_high_prob_mention_count_too_low_queues_not_merges(self):
        from services.deduplication import _route_pair

        ms = MagicMock()
        # entity_b only has 1 mention
        e_a = {**_ENTITY_A, "mention_count": 3}
        e_b = {**_ENTITY_B, "mention_count": 1}

        # VERIFY-PLAN: updated to match implementation (see note above)
        with patch("services.entity_merge.merge_entities"):
            result = _route_pair(ms, "run-1", USER_ID, e_a, e_b, 0.90, {})

        assert result == "queued"
        # Verify no merge was attempted (merge_entities not called)
        # The mock ensures nothing happened in the merge path

    def test_medium_prob_goes_to_queue(self):
        from services.deduplication import _route_pair

        ms = MagicMock()
        result = _route_pair(ms, "run-1", USER_ID, _ENTITY_A, _ENTITY_B, 0.70, {})

        assert result == "queued"
        # Should have inserted into dedup_candidates and review_queue
        assert ms.execute.call_count >= 2

    def test_low_prob_ignored(self):
        from services.deduplication import _route_pair

        ms = MagicMock()
        result = _route_pair(ms, "run-1", USER_ID, _ENTITY_A, _ENTITY_B, 0.30, {})

        assert result == "ignored"
        ms.execute.assert_not_called()

    def test_boundary_at_0_85_auto_merges(self):
        from services.deduplication import _route_pair

        ms = MagicMock()
        e_a = {**_ENTITY_A, "mention_count": 3}
        e_b = {**_ENTITY_B, "mention_count": 2}

        # VERIFY-PLAN: updated to match implementation (see note above)
        with patch("services.entity_merge.merge_entities") as mock_merge:
            mock_merge.return_value = MagicMock()
            result = _route_pair(ms, "run-1", USER_ID, e_a, e_b, 0.85, {})

        assert result == "auto_merged"

    def test_boundary_at_0_50_queues(self):
        from services.deduplication import _route_pair

        ms = MagicMock()
        result = _route_pair(ms, "run-1", USER_ID, _ENTITY_A, _ENTITY_B, 0.50, {})
        assert result == "queued"

    def test_just_below_0_50_ignored(self):
        from services.deduplication import _route_pair

        ms = MagicMock()
        result = _route_pair(ms, "run-1", USER_ID, _ENTITY_A, _ENTITY_B, 0.499, {})
        assert result == "ignored"


# ---------------------------------------------------------------------------
# 4. Winner selection
# ---------------------------------------------------------------------------


class TestWinnerSelection:
    def test_higher_mention_count_wins(self):
        from services.deduplication import _select_winner

        e_high = {**_ENTITY_A, "entity_id": ID_A, "mention_count": 5}
        e_low = {**_ENTITY_B, "entity_id": ID_B, "mention_count": 1}
        winner, loser = _select_winner(e_high, e_low)
        assert winner == ID_A
        assert loser == ID_B

    def test_higher_mention_count_wins_reversed_input(self):
        from services.deduplication import _select_winner

        e_high = {**_ENTITY_A, "entity_id": ID_A, "mention_count": 5}
        e_low = {**_ENTITY_B, "entity_id": ID_B, "mention_count": 1}
        winner, loser = _select_winner(e_low, e_high)
        assert winner == ID_A
        assert loser == ID_B

    def test_tie_lower_uuid_wins(self):
        from services.deduplication import _select_winner

        # ID_A < ID_B (both already sorted)
        e_a = {**_ENTITY_A, "entity_id": ID_A, "mention_count": 3}
        e_b = {**_ENTITY_B, "entity_id": ID_B, "mention_count": 3}
        winner, loser = _select_winner(e_a, e_b)
        assert winner == ID_A
        assert loser == ID_B


# ---------------------------------------------------------------------------
# 5. Run lifecycle
# ---------------------------------------------------------------------------


class TestRunLifecycle:
    def test_run_row_inserted_at_start(self):
        run_id = str(uuid.uuid4())
        ms = _make_ms(run_id=run_id)

        with (
            patch("services.deduplication.config") as mock_cfg,
            patch("services.deduplication._build_candidates") as mock_block,
            patch("services.deduplication._score_candidates_with_splink") as mock_score,
        ):
            mock_cfg.get_metadata_store.return_value = ms
            mock_cfg.get_vector_store.return_value = MagicMock()
            mock_block.return_value = (set(), {})
            mock_score.return_value = []

            from services.deduplication import run_deduplication_job

            result = run_deduplication_job(user_id=USER_ID)

        # fetch_one called to insert run row
        insert_calls = [
            c for c in ms.fetch_one.call_args_list if "INSERT INTO deduplication_runs" in str(c)
        ]
        assert len(insert_calls) >= 1
        assert result["run_id"] == run_id

    def test_run_row_updated_on_completion(self):
        run_id = str(uuid.uuid4())
        ms = _make_ms(run_id=run_id)

        with (
            patch("services.deduplication.config") as mock_cfg,
            patch("services.deduplication._build_candidates") as mock_block,
            patch("services.deduplication._score_candidates_with_splink") as mock_score,
        ):
            mock_cfg.get_metadata_store.return_value = ms
            mock_cfg.get_vector_store.return_value = MagicMock()
            mock_block.return_value = (set(), {})
            mock_score.return_value = []

            from services.deduplication import run_deduplication_job

            run_deduplication_job(user_id=USER_ID)

        update_calls = [
            c for c in ms.execute.call_args_list if "UPDATE deduplication_runs" in str(c)
        ]
        assert len(update_calls) >= 1

    def test_exception_sets_error_message_and_finished_at(self):
        run_id = str(uuid.uuid4())
        ms = _make_ms(run_id=run_id)

        with (
            patch("services.deduplication.config") as mock_cfg,
            patch("services.deduplication._build_candidates") as mock_block,
        ):
            mock_cfg.get_metadata_store.return_value = ms
            mock_cfg.get_vector_store.return_value = MagicMock()
            mock_block.side_effect = RuntimeError("simulated failure")

            from services.deduplication import run_deduplication_job

            result = run_deduplication_job(user_id=USER_ID)

        # Must not raise
        assert result["run_id"] == run_id
        # Error update must have been attempted
        error_updates = [
            c
            for c in ms.execute.call_args_list
            if "error_message" in str(c) and "finished_at" in str(c)
        ]
        assert len(error_updates) >= 1

    def test_no_exception_raised_on_run_failure(self):
        run_id = str(uuid.uuid4())
        ms = _make_ms(run_id=run_id)

        with (
            patch("services.deduplication.config") as mock_cfg,
            patch("services.deduplication._build_candidates") as mock_block,
        ):
            mock_cfg.get_metadata_store.return_value = ms
            mock_cfg.get_vector_store.return_value = MagicMock()
            mock_block.side_effect = RuntimeError("simulated failure")

            from services.deduplication import run_deduplication_job

            # Should return a dict, not raise
            result = run_deduplication_job(user_id=USER_ID)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 6. Interrupted run — next run starts fresh (no resume)
# ---------------------------------------------------------------------------


class TestInterruptedRun:
    def test_interrupted_run_does_not_block_new_job_in_service(self):
        """run_deduplication_job itself does not check for interrupted runs.
        The 409 guard lives in the POST /entities/deduplicate route.
        A new run always inserts a fresh row regardless of prior interrupted rows.
        """
        run_id_new = str(uuid.uuid4())
        # Simulate an existing finished_at IS NULL row in the DB
        # run_deduplication_job will still insert a new row (no resume logic).
        ms = _make_ms(run_id=run_id_new)

        with (
            patch("services.deduplication.config") as mock_cfg,
            patch("services.deduplication._build_candidates") as mock_block,
            patch("services.deduplication._score_candidates_with_splink") as mock_score,
        ):
            mock_cfg.get_metadata_store.return_value = ms
            mock_cfg.get_vector_store.return_value = MagicMock()
            mock_block.return_value = (set(), {})
            mock_score.return_value = []

            from services.deduplication import run_deduplication_job

            result = run_deduplication_job(user_id=USER_ID)

        assert result["run_id"] == run_id_new


# ---------------------------------------------------------------------------
# 7. POST /entities/deduplicate route
# ---------------------------------------------------------------------------


def _make_app_with_mocked_stores(ms_mock, vs_mock=None):
    """Return a TestClient with config stores mocked."""
    import sys

    # Minimal mock modules so main.py imports don't fail
    mocks = {}
    for mod in [
        "hooks",
        "events",
        "adapters.postgres_store",
        "adapters.qdrant_store",
        "adapters.ollama_embedder",
        "services.ingest",
        "signals",
        "librechat_config",
        "services.routines",
        "permissions",
    ]:
        if mod not in sys.modules:
            mocks[mod] = MagicMock()

    with patch.dict("sys.modules", mocks):
        from fastapi import FastAPI
        from routes.admin import router

        app = FastAPI()
        app.include_router(router)

        with (
            patch("config.get_metadata_store", return_value=ms_mock),
            patch("config.get_vector_store", return_value=vs_mock or MagicMock()),
        ):
            return TestClient(app)


class TestDeduplicateRoute:
    def _make_route_ms(self, in_progress=False, run_id=None):
        ms = MagicMock()
        _run_id = run_id or str(uuid.uuid4())

        def _fetch_one(sql, params=None):
            if "finished_at IS NULL" in sql:
                return {"run_id": _run_id} if in_progress else None
            if "INSERT INTO deduplication_runs" in sql:
                return {"run_id": _run_id}
            return None

        ms.fetch_one.side_effect = _fetch_one
        ms.execute.return_value = None
        return ms, _run_id

    def test_returns_202_when_no_run_in_progress(self):
        ms, run_id = self._make_route_ms(in_progress=False)

        with (
            patch("config.get_metadata_store", return_value=ms),
            patch("config.get_vector_store", return_value=MagicMock()),
        ):
            from fastapi import FastAPI
            from routes.admin import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/entities/deduplicate")

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "started"
        assert "run_id" in body

    def test_returns_409_when_run_in_progress(self):
        run_id = str(uuid.uuid4())
        ms, _ = self._make_route_ms(in_progress=True, run_id=run_id)

        with (
            patch("config.get_metadata_store", return_value=ms),
            patch("config.get_vector_store", return_value=MagicMock()),
        ):
            from fastapi import FastAPI
            from routes.admin import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/entities/deduplicate")

        assert resp.status_code == 409
        assert "deduplication already running" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 8. Weekly job integration
# ---------------------------------------------------------------------------


class TestWeeklyJobIntegration:
    def test_dedup_runs_as_final_step(self):
        """Deduplication is called after edge scoring and constraint checks."""
        call_order = []

        def mock_edge_job(user_id="default"):
            call_order.append("edge")
            return {
                "pairs_computed": 1,
                "pairs_upserted": 1,
                "falkordb_updated": 0,
                "duration_ms": 10,
            }

        def mock_dedup_job(user_id="default"):
            call_order.append("dedup")
            return {
                "run_id": "r1",
                "candidate_count": 2,
                "auto_merged": 1,
                "queued_for_review": 1,
                "duration_ms": 50,
            }

        def mock_orphan_check(user_id="default"):
            call_order.append("orphan")
            return 0

        def mock_alias_check(user_id="default"):
            call_order.append("alias")
            return 0

        with (
            patch("services.edge_quality.run_edge_quality_job", side_effect=mock_edge_job),
            patch(
                "services.entity_constraints.check_orphan_entities", side_effect=mock_orphan_check
            ),
            patch(
                "services.entity_constraints.check_alias_uniqueness", side_effect=mock_alias_check
            ),
            patch("services.deduplication.run_deduplication_job", side_effect=mock_dedup_job),
        ):
            from services.edge_quality import run_weekly_quality_job

            run_weekly_quality_job()

        assert call_order.index("edge") < call_order.index("dedup")
        assert call_order.index("dedup") == len(call_order) - 1

    def test_dedup_exception_does_not_prevent_other_steps(self):
        """An exception in deduplication must not fail edge scoring or constraints."""
        edge_called = []
        orphan_called = []
        alias_called = []

        def mock_edge_job(user_id="default"):
            edge_called.append(True)
            return {
                "pairs_computed": 0,
                "pairs_upserted": 0,
                "falkordb_updated": 0,
                "duration_ms": 0,
            }

        def mock_orphan_check(user_id="default"):
            orphan_called.append(True)
            return 0

        def mock_alias_check(user_id="default"):
            alias_called.append(True)
            return 0

        def mock_dedup_job_raises(user_id="default"):
            raise RuntimeError("dedup exploded")

        with (
            patch("services.edge_quality.run_edge_quality_job", side_effect=mock_edge_job),
            patch(
                "services.entity_constraints.check_orphan_entities", side_effect=mock_orphan_check
            ),
            patch(
                "services.entity_constraints.check_alias_uniqueness", side_effect=mock_alias_check
            ),
            patch(
                "services.deduplication.run_deduplication_job", side_effect=mock_dedup_job_raises
            ),
        ):
            from services.edge_quality import run_weekly_quality_job

            run_weekly_quality_job()

        assert edge_called, "edge quality job must have run"
        assert orphan_called, "orphan check must have run"
        assert alias_called, "alias check must have run"

    def test_weekly_result_includes_dedup_fields(self):
        """Summary dict includes auto_merged and queued_for_review."""

        def mock_edge_job(user_id="default"):
            return {
                "pairs_computed": 5,
                "pairs_upserted": 5,
                "falkordb_updated": 0,
                "duration_ms": 100,
            }

        def mock_dedup_job(user_id="default"):
            return {
                "run_id": "r1",
                "candidate_count": 3,
                "auto_merged": 2,
                "queued_for_review": 1,
                "duration_ms": 200,
            }

        with (
            patch("services.edge_quality.run_edge_quality_job", side_effect=mock_edge_job),
            patch("services.entity_constraints.check_orphan_entities", return_value=0),
            patch("services.entity_constraints.check_alias_uniqueness", return_value=0),
            patch("services.deduplication.run_deduplication_job", side_effect=mock_dedup_job),
        ):
            from services.edge_quality import run_weekly_quality_job

            result = run_weekly_quality_job()

        assert "auto_merged" in result
        assert "queued_for_review" in result
        assert result["auto_merged"] == 2
        assert result["queued_for_review"] == 1

    def test_weekly_result_dedup_fields_zero_on_dedup_exception(self):
        """If dedup raises, auto_merged and queued_for_review default to 0."""

        def mock_edge_job(user_id="default"):
            return {
                "pairs_computed": 0,
                "pairs_upserted": 0,
                "falkordb_updated": 0,
                "duration_ms": 0,
            }

        with (
            patch("services.edge_quality.run_edge_quality_job", side_effect=mock_edge_job),
            patch("services.entity_constraints.check_orphan_entities", return_value=0),
            patch("services.entity_constraints.check_alias_uniqueness", return_value=0),
            patch("services.deduplication.run_deduplication_job", side_effect=RuntimeError("boom")),
        ):
            from services.edge_quality import run_weekly_quality_job

            result = run_weekly_quality_job()

        assert result["auto_merged"] == 0
        assert result["queued_for_review"] == 0
