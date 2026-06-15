# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for async Ollama pull jobs (LUM-449)."""

import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

import ollama_client
from services.ollama_pull_jobs import JobAlreadyRunning
from services.ollama_pull_jobs import create_job
from services.ollama_pull_jobs import get_active_job
from services.ollama_pull_jobs import job_to_response
from services.ollama_pull_jobs import run_pull_job


class TestIterPullProgress(unittest.TestCase):
    def test_iter_pull_progress_parses_ndjson(self):
        lines = [
            b'{"status":"downloading","total":100,"completed":50}',
            b'{"status":"success"}',
        ]

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def raise_for_status(self):
                return None

            def iter_lines(self):
                yield from lines

        with patch("ollama_client.httpx.stream", return_value=FakeResponse()):
            events = list(ollama_client.iter_pull_progress("tinyllama"))

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["progress_pct"], 50)
        self.assertEqual(events[0]["status"], "downloading")


class TestOllamaPullJobsService(unittest.TestCase):
    def test_create_job_409_when_running(self):
        meta = MagicMock()
        meta.fetch_one.return_value = {"job_id": "existing"}
        with patch("services.ollama_pull_jobs._meta", return_value=meta):
            with self.assertRaises(JobAlreadyRunning):
                create_job("tinyllama")

    def test_get_active_job_returns_running(self):
        meta = MagicMock()
        meta.fetch_one.return_value = {
            "job_id": "j1",
            "model_name": "m",
            "status": "running",
            "progress_pct": 10,
            "status_message": "pulling",
            "error_message": None,
            "qdrant_init_warning": None,
            "created_at": None,
            "started_at": None,
            "finished_at": None,
        }
        with patch("services.ollama_pull_jobs._meta", return_value=meta):
            row = get_active_job()
        self.assertEqual(row["status"], "running")

    def test_run_pull_job_success_updates_row(self):
        meta = MagicMock()
        meta.fetch_one.return_value = {
            "job_id": "j1",
            "model_name": "tinyllama",
            "status": "pending",
        }
        events = [
            {"status": "downloading", "completed": 50, "total": 100, "progress_pct": 50},
            {"status": "success", "completed": None, "total": None, "progress_pct": None},
        ]

        with (
            patch("services.ollama_pull_jobs._meta", return_value=meta),
            patch("services.ollama_pull_jobs.get_job", side_effect=lambda jid: meta.fetch_one()),
            patch("ollama_client.iter_pull_progress", return_value=iter(events)),
            patch("services.ollama_pull_jobs.finalize_ollama_pull", return_value=None),
        ):
            run_pull_job("j1", MagicMock())

        finish_calls = [
            c for c in meta.execute.call_args_list if c.args and c.args[0].startswith("UPDATE ollama_pull_jobs SET status")
        ]
        self.assertTrue(any(call.args[1][0] == "succeeded" for call in finish_calls))

    def test_run_pull_job_ollama_error(self):
        meta = MagicMock()
        meta.fetch_one.return_value = {
            "job_id": "j1",
            "model_name": "tinyllama",
            "status": "pending",
        }

        with (
            patch("services.ollama_pull_jobs._meta", return_value=meta),
            patch("services.ollama_pull_jobs.get_job", side_effect=lambda jid: meta.fetch_one()),
            patch("ollama_client.iter_pull_progress", side_effect=RuntimeError("pull failed")),
        ):
            run_pull_job("j1", MagicMock())

        fail_calls = [
            c for c in meta.execute.call_args_list if c.args and c.args[0].startswith("UPDATE ollama_pull_jobs SET status")
        ]
        self.assertTrue(any(call.args[1][0] == "failed" for call in fail_calls))

    def test_finalize_qdrant_warning_on_init_failure(self):
        from services.ollama_pull_jobs import QDRANT_INIT_WARNING_MSG
        from services.ollama_pull_jobs import finalize_ollama_pull

        embedder = MagicMock()
        embedder.ping.return_value = True
        embedder.vector_size = 768
        vs = MagicMock()
        vs.create_collection.side_effect = RuntimeError("qdrant down")

        with (
            patch("routes.admin._sync_librechat_config"),
            patch("config.get_embedder", return_value=embedder),
            patch("config.get_vector_store", return_value=vs),
            patch.dict("os.environ", {"EMBEDDING_MODEL": "nomic-embed-text"}),
        ):
            app_state = MagicMock()
            app_state.embedding_ready = False
            warning = finalize_ollama_pull("nomic-embed-text", app_state)

        self.assertEqual(warning, QDRANT_INIT_WARNING_MSG)

    def test_stale_running_marked_failed_on_new_create(self):
        meta = MagicMock()
        meta.fetch_one.side_effect = [
            None,
            {"job_id": "new-id"},
        ]

        with patch("services.ollama_pull_jobs._meta", return_value=meta):
            job_id = create_job("tinyllama")

        stale_calls = [
            c
            for c in meta.execute.call_args_list
            if c.args and "stale (orchestrator restart?)" in str(c.args)
        ]
        self.assertTrue(stale_calls)
        self.assertEqual(job_id, "new-id")

    def test_progress_update_throttled(self):
        meta = MagicMock()
        meta.fetch_one.return_value = {
            "job_id": "j1",
            "model_name": "tinyllama",
            "status": "pending",
        }
        events = [
            {"status": "downloading", "completed": 1, "total": 100, "progress_pct": 1},
            {"status": "downloading", "completed": 2, "total": 100, "progress_pct": 2},
            {"status": "success", "completed": None, "total": None, "progress_pct": None},
        ]

        with (
            patch("services.ollama_pull_jobs._meta", return_value=meta),
            patch("services.ollama_pull_jobs.get_job", side_effect=lambda jid: meta.fetch_one()),
            patch("ollama_client.iter_pull_progress", return_value=iter(events)),
            patch("services.ollama_pull_jobs.finalize_ollama_pull", return_value=None),
            patch("services.ollama_pull_jobs.time.monotonic", side_effect=[0.0, 0.1, 0.2, 2.0]),
        ):
            run_pull_job("j1", MagicMock())

        progress_updates = [
            c
            for c in meta.execute.call_args_list
            if c.args and "progress_pct" in str(c.args[0])
        ]
        self.assertLessEqual(len(progress_updates), 2)

    def test_job_to_response_shape(self):
        payload = job_to_response(
            {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "model_name": "tinyllama",
                "status": "running",
                "progress_pct": 42,
                "status_message": "downloading",
                "error_message": None,
                "qdrant_init_warning": None,
                "created_at": None,
                "started_at": None,
                "finished_at": None,
            }
        )
        self.assertEqual(payload["job_id"], "550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(payload["progress_pct"], 42)
        self.assertIn("model_name", payload)


if __name__ == "__main__":
    unittest.main()
