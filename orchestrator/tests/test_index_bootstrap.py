# SPDX-License-Identifier: AGPL-3.0-only
"""Bundled Hub index bootstrap status on ``GET /healthz``."""

from __future__ import annotations

import main
from fastapi.testclient import TestClient
from services.index_bootstrap import (
    begin_folder_scan,
    complete_folder_scan,
    index_bootstrap_status,
    mark_scan_queued,
    prior_library_index_exists,
    report_folder_scan_progress,
    reset_scan_state,
)


def test_index_bootstrap_status_lifecycle() -> None:
    # Module-global scan state may be dirtied by other suites; start clean.
    reset_scan_state()
    assert index_bootstrap_status()["index_scan"] == "idle"

    mark_scan_queued()
    assert index_bootstrap_status()["index_scan"] == "queued"

    begin_folder_scan(total_files=3)
    status = index_bootstrap_status()
    assert status["index_scan"] == "scanning"
    assert status["index_scan_total"] == "3"
    assert status["index_scan_done"] == "0"

    report_folder_scan_progress(done=2, total=3)
    assert index_bootstrap_status()["index_scan_done"] == "2"

    complete_folder_scan(total_files=3, ingested=2, skipped=1, errors=0)
    done = index_bootstrap_status()
    assert done["index_scan"] == "ready"
    assert done["index_scan_done"] == "3"


def test_bundled_start_library_index_not_available_without_defer_flag(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LUMOGIS_DEFER_LIBRARY_INDEX", raising=False)
    with TestClient(main.app) as client:
        resp = client.post("/bundled/start-library-index")
    assert resp.status_code == 404


def test_bundled_start_library_index_queues_when_ready(monkeypatch) -> None:
    from unittest.mock import patch

    monkeypatch.setenv("LUMOGIS_DEFER_LIBRARY_INDEX", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    with TestClient(main.app) as client:
        client.app.state.embedding_ready = True
        with patch(
            "services.ingest.enqueue_initial_ingest_scan",
            return_value=True,
        ) as enqueue:
            resp = client.post("/bundled/start-library-index")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    enqueue.assert_called_once()


def test_prior_library_index_exists_false_without_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.index_bootstrap._file_index_count",
        lambda: 0,
    )
    assert prior_library_index_exists() is False


def test_prior_library_index_exists_true_when_rows_present(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.index_bootstrap._file_index_count",
        lambda: 3,
    )
    assert prior_library_index_exists() is True


def test_library_resync_on_start_env_gate(monkeypatch) -> None:
    from main import _library_resync_on_start

    monkeypatch.delenv("LUMOGIS_LIBRARY_RESYNC_ON_START", raising=False)
    assert _library_resync_on_start() is False
    monkeypatch.setenv("LUMOGIS_LIBRARY_RESYNC_ON_START", "1")
    assert _library_resync_on_start() is True


def test_healthz_includes_index_and_embedding_fields(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    with TestClient(main.app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["embedding_ready"] in ("true", "false")
    assert "index_scan" in body
    assert "index_file_count" in body
    for key, value in body.items():
        if isinstance(value, str):
            assert "/workspace" not in value
            if "path" in key.lower():
                assert not value.startswith("/")
