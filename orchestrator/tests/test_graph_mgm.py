# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the KG Management Page endpoints.

Pass 1 backend endpoints:
  - GET /graph/mgm returns 200 with HTML content-type
  - GET /graph/mgm returns 404 when the HTML file does not exist
  - GET /kg/job-status returns correct shape with all three job fields
  - GET /kg/job-status returns nulls gracefully when no rows exist
  - GET /kg/job-status returns partial data when one field query fails
  - POST /kg/trigger-weekly returns 202 when no dedup job is running
  - POST /kg/trigger-weekly returns 409 when a dedup job is already running
  - viz_routes._require_auth() no longer raises NameError (import os fix)

Pass 2 — Graph tab API coverage:
  The Graph tab in graph_mgm.html calls the following existing endpoints:
    GET /graph/ego, GET /graph/path, GET /graph/search, GET /graph/stats
  These endpoints are fully covered by:
    orchestrator/tests/test_viz_routes.py
  No new backend tests are required for Pass 2.

Pass 3 — Review Queue tab API coverage:
  The Review Queue tab calls the following existing endpoints:
    GET /review-queue?source=all  — unified queue with all four item types
    POST /review-queue/decide     — action handler (merge, distinct, promote, discard, suppress, dismiss)
  These endpoints are fully covered by:
    orchestrator/tests/test_review_queue.py
  No new backend tests are required for Pass 3.

Pass 3 pre-flight fixes:
  - Alpine.js CDN updated to 3.14.8 in graph_mgm.html
  - GET /graph/stats now returns cooccurrence_threshold via config.get_cooccurrence_threshold()
    (was missing from response; graphCoocThreshold slider floor label now reflects live settings)
  - localStorage key aligned: dashboard/index.html changed from 'lg-theme' to 'lm-theme'
    to match graph_viz.html and graph_mgm.html (all three pages now share 'lm-theme')

Pass 4 — Stop entity list endpoints (covered below):
  GET /kg/stop-entities, POST /kg/stop-entities
  Settings tab calls GET/POST/DELETE /kg/settings — covered by test_kg_settings.py.

Pass 5 — Polish and close-out (no new backend endpoints; frontend-only changes):
  - LibreChat footer updated: /graph/viz → /graph/mgm in librechat_config.py and
    config/librechat.coldstart.yaml
  - stop_entities.txt header warning added
  - Alpine.js CDN comment corrected to 3.14.8
  - Toast auto-dismiss timeout corrected to 5 000 ms (was 4 000 ms)
  - Responsive CSS added (@media max-width: 768px)
  No new backend tests required for Pass 5.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meta_store(
    *,
    settings_rows: list[dict] | None = None,
    dedup_finished_row: dict | None = None,
    dedup_running_row: dict | None = None,
    raise_on: str | None = None,
):
    """Return a MagicMock MetadataStore with configurable responses."""
    ms = MagicMock()

    def _fetch_one(sql, *args, **kwargs):
        sql_lower = sql.lower()
        if raise_on and raise_on in sql_lower:
            raise Exception("simulated DB failure")
        if "_job_last_reconciliation" in sql:
            return next(
                (r for r in (settings_rows or []) if r.get("key") == "_job_last_reconciliation"),
                None,
            )
        if "_job_last_weekly" in sql:
            return next(
                (r for r in (settings_rows or []) if r.get("key") == "_job_last_weekly"),
                None,
            )
        if "finished_at is not null" in sql_lower:
            return dedup_finished_row
        if "finished_at is null" in sql_lower:
            return dedup_running_row
        return None

    ms.fetch_one.side_effect = _fetch_one
    ms.execute.return_value = None
    return ms


# ---------------------------------------------------------------------------
# 1. GET /graph/mgm — file exists
# ---------------------------------------------------------------------------


class TestGraphMgmServe(unittest.TestCase):
    def test_returns_200_with_html(self):
        from fastapi.responses import FileResponse
        from routes.admin import graph_mgm

        fake_path = Path("/tmp/fake_graph_mgm.html")
        fake_path.write_text("<html><body>ok</body></html>")
        try:
            with patch("routes.admin._GRAPH_MGM_HTML", fake_path):
                result = graph_mgm()
            self.assertIsInstance(result, FileResponse)
            self.assertEqual(result.media_type, "text/html")
        finally:
            fake_path.unlink(missing_ok=True)

    def test_returns_404_when_file_missing(self):
        from fastapi import HTTPException
        from routes.admin import graph_mgm

        missing_path = Path("/tmp/does_not_exist_graph_mgm.html")
        if missing_path.exists():
            missing_path.unlink()

        with patch("routes.admin._GRAPH_MGM_HTML", missing_path):
            with self.assertRaises(HTTPException) as ctx:
                graph_mgm()
        self.assertEqual(ctx.exception.status_code, 404)


# ---------------------------------------------------------------------------
# 2. GET /kg/job-status — correct shape
# ---------------------------------------------------------------------------


class TestKgJobStatusShape(unittest.TestCase):
    def _call(self, meta_store):
        from routes.admin import kg_job_status

        with patch("config.get_metadata_store", return_value=meta_store):
            return kg_job_status()

    def test_all_three_job_fields_present(self):
        ms = _make_meta_store()
        result = self._call(ms)
        self.assertIn("reconciliation", result)
        self.assertIn("weekly_quality", result)
        self.assertIn("deduplication", result)
        # reconciliation sub-keys
        self.assertIn("last_run", result["reconciliation"])
        # weekly_quality sub-keys
        self.assertIn("last_run", result["weekly_quality"])
        # deduplication sub-keys
        self.assertIn("last_run", result["deduplication"])
        self.assertIn("running", result["deduplication"])
        self.assertIn("last_auto_merged", result["deduplication"])
        self.assertIn("last_queued_for_review", result["deduplication"])
        self.assertIn("last_candidate_count", result["deduplication"])


# ---------------------------------------------------------------------------
# 3. GET /kg/job-status — graceful nulls when no data
# ---------------------------------------------------------------------------


class TestKgJobStatusNulls(unittest.TestCase):
    def _call(self, meta_store):
        from routes.admin import kg_job_status

        with patch("config.get_metadata_store", return_value=meta_store):
            return kg_job_status()

    def test_all_nulls_when_no_rows(self):
        ms = _make_meta_store()
        result = self._call(ms)
        self.assertIsNone(result["reconciliation"]["last_run"])
        self.assertIsNone(result["weekly_quality"]["last_run"])
        self.assertIsNone(result["deduplication"]["last_run"])
        self.assertFalse(result["deduplication"]["running"])
        self.assertIsNone(result["deduplication"]["last_auto_merged"])

    def test_timestamps_populated_when_rows_exist(self):
        import datetime

        ts = "2026-04-15T02:00:00+00:00"
        ms = _make_meta_store(
            settings_rows=[
                {"key": "_job_last_reconciliation", "value": ts},
                {"key": "_job_last_weekly", "value": ts},
            ],
            dedup_finished_row={
                "finished_at": datetime.datetime(2026, 4, 14, 2, 0, 0),
                "auto_merged": 5,
                "queued_for_review": 2,
                "candidate_count": 12,
            },
        )
        result = self._call(ms)
        self.assertEqual(result["reconciliation"]["last_run"], ts)
        self.assertEqual(result["weekly_quality"]["last_run"], ts)
        self.assertIsNotNone(result["deduplication"]["last_run"])
        self.assertEqual(result["deduplication"]["last_auto_merged"], 5)
        self.assertEqual(result["deduplication"]["last_queued_for_review"], 2)
        self.assertEqual(result["deduplication"]["last_candidate_count"], 12)

    def test_running_true_when_in_progress_row_exists(self):
        ms = _make_meta_store(
            dedup_running_row={"run_id": "abc123"},
        )
        result = self._call(ms)
        self.assertTrue(result["deduplication"]["running"])


# ---------------------------------------------------------------------------
# 4. GET /kg/job-status — partial data when one query fails
# ---------------------------------------------------------------------------


class TestKgJobStatusPartial(unittest.TestCase):
    def test_dedup_failure_still_returns_timestamp_data(self):
        """If the deduplication_runs query fails, reconciliation/weekly still returned."""
        ts = "2026-04-15T02:00:00+00:00"

        ms = MagicMock()

        def _fetch_one(sql, *args, **kwargs):
            sql_lower = sql.lower()
            if "_job_last_reconciliation" in sql:
                return {"value": ts}
            if "_job_last_weekly" in sql:
                return {"value": ts}
            if "deduplication_runs" in sql_lower:
                raise Exception("dedup table unavailable")
            return None

        ms.fetch_one.side_effect = _fetch_one

        from routes.admin import kg_job_status

        with patch("config.get_metadata_store", return_value=ms):
            result = kg_job_status()

        # timestamps returned
        self.assertEqual(result["reconciliation"]["last_run"], ts)
        self.assertEqual(result["weekly_quality"]["last_run"], ts)
        # dedup gracefully null
        self.assertIsNone(result["deduplication"]["last_run"])
        self.assertFalse(result["deduplication"]["running"])


# ---------------------------------------------------------------------------
# 5. POST /kg/trigger-weekly — 202 when no dedup job running
# ---------------------------------------------------------------------------


class TestKgTriggerWeekly202(unittest.TestCase):
    def test_returns_202_response_dict_when_clear(self):
        ms = _make_meta_store(dedup_running_row=None)

        from fastapi import BackgroundTasks
        from routes.admin import kg_trigger_weekly

        bg = BackgroundTasks()
        with patch("config.get_metadata_store", return_value=ms):
            # The endpoint has status_code=202 on the decorator; the dict is the body.
            result = kg_trigger_weekly(bg)

        self.assertEqual(result["status"], "started")

    def test_background_task_is_added(self):
        ms = _make_meta_store(dedup_running_row=None)

        from fastapi import BackgroundTasks
        from routes.admin import kg_trigger_weekly

        bg = BackgroundTasks()
        with patch("config.get_metadata_store", return_value=ms):
            kg_trigger_weekly(bg)

        self.assertEqual(len(bg.tasks), 1)


# ---------------------------------------------------------------------------
# 6. POST /kg/trigger-weekly — 409 when dedup job is already running
# ---------------------------------------------------------------------------


class TestKgTriggerWeekly409(unittest.TestCase):
    def test_returns_409_when_dedup_running(self):
        ms = _make_meta_store(dedup_running_row={"run_id": "running-job-id"})

        from fastapi import BackgroundTasks
        from fastapi import HTTPException
        from routes.admin import kg_trigger_weekly

        bg = BackgroundTasks()
        with patch("config.get_metadata_store", return_value=ms):
            with self.assertRaises(HTTPException) as ctx:
                kg_trigger_weekly(bg)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("deduplication", ctx.exception.detail.lower())

    def test_no_background_task_added_on_409(self):
        ms = _make_meta_store(dedup_running_row={"run_id": "running-job-id"})

        from fastapi import BackgroundTasks
        from routes.admin import kg_trigger_weekly

        bg = BackgroundTasks()
        with patch("config.get_metadata_store", return_value=ms):
            try:
                kg_trigger_weekly(bg)
            except Exception:
                pass

        self.assertEqual(len(bg.tasks), 0)


# ---------------------------------------------------------------------------
# 7. viz_routes import os fix — module removed from Core (HTTP lives in lumogis-graph)
# ---------------------------------------------------------------------------


@unittest.skip("plugins.graph.viz_routes was removed from Core; HTTP lives in lumogis-graph.")
class TestVizRoutesImportOsFix(unittest.TestCase):
    def test_require_auth_does_not_raise_name_error(self):
        """Calling _require_auth should not raise NameError due to missing import os."""
        import sys

        # Force reimport of the module
        mod_name = "plugins.graph.viz_routes"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        from plugins.graph import viz_routes

        # _require_auth accesses os.environ — should not raise NameError
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None

        with patch("auth.get_user") as mock_get_user:
            mock_user = MagicMock()
            mock_user.is_authenticated = True
            mock_get_user.return_value = mock_user

            with patch.dict("os.environ", {"AUTH_ENABLED": "false"}):
                try:
                    viz_routes._require_auth(mock_request)
                except Exception as e:
                    self.assertNotIsInstance(
                        e,
                        NameError,
                        "NameError raised — likely missing 'import os' in viz_routes",
                    )


# ---------------------------------------------------------------------------
# Pass 4 — Stop entity list endpoints
# ---------------------------------------------------------------------------
#
# Pass 4 Settings tab API:
#   GET /kg/settings, POST /kg/settings, DELETE /kg/settings/{key}
#   are fully covered by: orchestrator/tests/test_kg_settings.py
#
#   GET /kg/stop-entities, POST /kg/stop-entities — covered below.


class TestStopEntitiesGet(unittest.TestCase):
    """GET /kg/stop-entities"""

    def _call(self, path_exists=True, file_content=None, read_raises=None):
        from unittest.mock import patch

        from routes.admin import kg_stop_entities_get

        fake_path = "/fake/stop_entities.txt"
        with patch("config.get_stop_entities_path", return_value=fake_path):
            if not path_exists:
                with patch("routes.admin._read_stop_entity_file", side_effect=FileNotFoundError()):
                    return kg_stop_entities_get()
            elif read_raises:
                with patch("routes.admin._read_stop_entity_file", side_effect=read_raises):
                    return kg_stop_entities_get()
            else:
                lines = file_content or []
                with patch("routes.admin._read_stop_entity_file", return_value=lines):
                    return kg_stop_entities_get()

    def test_correct_shape(self):
        result = self._call(file_content=["the meeting", "this project", "a call"])
        self.assertIn("phrases", result)
        self.assertIn("count", result)
        self.assertIn("source_path", result)
        self.assertEqual(result["count"], 3)
        self.assertEqual(len(result["phrases"]), 3)

    def test_phrases_sorted_alphabetically(self):
        result = self._call(file_content=["zebra", "apple", "mango"])
        self.assertEqual(result["phrases"], ["apple", "mango", "zebra"])

    def test_empty_list_when_file_missing(self):
        result = self._call(path_exists=False)
        self.assertEqual(result["phrases"], [])
        self.assertEqual(result["count"], 0)

    def test_500_when_file_unreadable(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self._call(read_raises=OSError("permission denied"))
        self.assertEqual(ctx.exception.status_code, 500)


class TestStopEntitiesPost(unittest.TestCase):
    """POST /kg/stop-entities"""

    def _add(self, phrase, existing=None):
        return self._call("add", phrase, existing or [])

    def _remove(self, phrase, existing=None):
        return self._call("remove", phrase, existing or ["the meeting", "this project"])

    def _call(self, action, phrase, existing_lines):
        from fastapi import HTTPException
        from routes.admin import StopEntityRequest
        from routes.admin import kg_stop_entities_post

        fake_path = "/fake/stop_entities.txt"
        body = StopEntityRequest(action=action, phrase=phrase)

        with (
            patch("config.get_stop_entities_path", return_value=fake_path),
            patch("routes.admin._read_stop_entity_file", return_value=list(existing_lines)),
            patch("routes.admin._write_stop_entity_file_atomic") as mock_write,
            patch("config.invalidate_settings_cache"),
        ):
            try:
                result = kg_stop_entities_post(body)
                return result, mock_write
            except HTTPException:
                raise

    def test_add_success_returns_new_count(self):
        result, mock_write = self._add("new phrase", existing=["the meeting"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 2)
        mock_write.assert_called_once()

    def test_add_duplicate_returns_400(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self._add("the meeting", existing=["the meeting", "this project"])
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("already in list", ctx.exception.detail)

    def test_remove_success_returns_new_count(self):
        result, mock_write = self._remove("the meeting")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        mock_write.assert_called_once()

    def test_remove_not_found_returns_400(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self._remove("nonexistent phrase")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("not found", ctx.exception.detail)

    def test_invalid_action_returns_400(self):
        from fastapi import HTTPException
        from routes.admin import StopEntityRequest
        from routes.admin import kg_stop_entities_post

        body = StopEntityRequest(action="delete", phrase="something")
        fake_path = "/fake/stop_entities.txt"
        with patch("config.get_stop_entities_path", return_value=fake_path):
            with self.assertRaises(HTTPException) as ctx:
                kg_stop_entities_post(body)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_phrase_too_long_returns_400(self):
        from fastapi import HTTPException
        from routes.admin import StopEntityRequest
        from routes.admin import kg_stop_entities_post

        body = StopEntityRequest(action="add", phrase="x" * 201)
        fake_path = "/fake/stop_entities.txt"
        with patch("config.get_stop_entities_path", return_value=fake_path):
            with self.assertRaises(HTTPException) as ctx:
                kg_stop_entities_post(body)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("200", ctx.exception.detail)

    def test_write_is_atomic(self):
        """Verify _write_stop_entity_file_atomic uses a temp file then os.replace."""
        import os as _os
        import tempfile as _tempfile

        fake_dir = "/tmp/lumogis_test_stop_entities"
        _os.makedirs(fake_dir, exist_ok=True)
        target = _os.path.join(fake_dir, "stop_entities.txt")

        from routes.admin import _write_stop_entity_file_atomic

        # Patch tempfile.mkstemp and os.replace to verify the atomic pattern
        written_files = []

        original_mkstemp = _tempfile.mkstemp
        original_replace = _os.replace

        def fake_mkstemp(dir, prefix, suffix):
            fd, tmp = original_mkstemp(dir=dir, prefix=prefix, suffix=suffix)
            written_files.append(("mkstemp", tmp))
            return fd, tmp

        def fake_replace(src, dst):
            written_files.append(("replace", src, dst))
            original_replace(src, dst)

        with (
            patch("tempfile.mkstemp", side_effect=fake_mkstemp),
            patch("os.replace", side_effect=fake_replace),
        ):
            _write_stop_entity_file_atomic(target, ["phrase one", "phrase two"])

        # mkstemp was called first, then os.replace
        ops = [w[0] for w in written_files]
        self.assertIn("mkstemp", ops)
        self.assertIn("replace", ops)
        mkstemp_idx = ops.index("mkstemp")
        replace_idx = ops.index("replace")
        self.assertLess(mkstemp_idx, replace_idx, "mkstemp must precede os.replace")

        # Destination must be the target path
        replace_op = next(w for w in written_files if w[0] == "replace")
        self.assertEqual(replace_op[2], target)

        # Cleanup
        try:
            _os.unlink(target)
            _os.rmdir(fake_dir)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
