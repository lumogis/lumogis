# SPDX-License-Identifier: AGPL-3.0-only
"""Bundled Hub index bootstrap status on ``GET /healthz``."""

from __future__ import annotations

from unittest.mock import patch

import main
from fastapi.testclient import TestClient
from services.index_bootstrap import begin_folder_scan
from services.index_bootstrap import complete_folder_scan
from services.index_bootstrap import index_bootstrap_status
from services.index_bootstrap import mark_scan_queued
from services.index_bootstrap import prior_library_index_exists
from services.index_bootstrap import report_folder_scan_progress
from services.index_bootstrap import reset_scan_state


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


# ---------------------------------------------------------------------------
# LUM-478: lifespan integration — defer + resync path (ADR-096)
# ---------------------------------------------------------------------------


def _patch_embedding_ready(monkeypatch) -> None:
    """Make the embedder appear ready during lifespan startup."""

    def _activate(app_state):
        app_state.embedding_ready = True
        return True

    monkeypatch.setattr(
        "services.embedding_readiness.try_activate_embedding",
        _activate,
    )


def test_lifespan_defer_resync_enqueues_when_prior_index_exists(monkeypatch) -> None:
    """Lifespan enqueues resync when defer flag set, prior index present, embedder ready (LUM-478).

    This is the ADR-096 cold-start path: Hub was fully quit, files were dropped
    into an ingest folder, Core restarts. Scan must be queued even though the
    wizard already ran (LUMOGIS_DEFER_LIBRARY_INDEX is still set in env).

    Patches _file_index_count (not prior_library_index_exists) so the real
    helper executes inside the lifespan and the gate logic is covered.
    """
    monkeypatch.setenv("LUMOGIS_DEFER_LIBRARY_INDEX", "1")
    monkeypatch.setenv("LUMOGIS_LIBRARY_RESYNC_ON_START", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _patch_embedding_ready(monkeypatch)
    monkeypatch.setattr("services.index_bootstrap._file_index_count", lambda: 3)

    with patch("services.ingest.enqueue_initial_ingest_scan", return_value=True) as enqueue:
        with TestClient(main.app):
            pass  # lifespan startup is the unit under test

    enqueue.assert_called_once()


def test_lifespan_defer_skips_resync_when_no_prior_index(monkeypatch) -> None:
    """Lifespan does NOT enqueue resync when file_index is empty (first-run wizard path, LUM-478).

    When LUMOGIS_DEFER_LIBRARY_INDEX is set and file_index has no rows, Core
    must stay silent so the Hub wizard controls the first bulk scan via
    POST /bundled/start-library-index.
    """
    monkeypatch.setenv("LUMOGIS_DEFER_LIBRARY_INDEX", "1")
    monkeypatch.setenv("LUMOGIS_LIBRARY_RESYNC_ON_START", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _patch_embedding_ready(monkeypatch)
    monkeypatch.setattr("services.index_bootstrap._file_index_count", lambda: 0)

    with patch("services.ingest.enqueue_initial_ingest_scan", return_value=True) as enqueue:
        with TestClient(main.app):
            pass

    enqueue.assert_not_called()


def test_lifespan_defer_skips_resync_when_embedder_not_ready(monkeypatch) -> None:
    """Lifespan does NOT enqueue resync when embedder is not ready at startup (LUM-478).

    The retry job (embedding_readiness_retry) picks it up once the model is warm.
    """
    monkeypatch.setenv("LUMOGIS_DEFER_LIBRARY_INDEX", "1")
    monkeypatch.setenv("LUMOGIS_LIBRARY_RESYNC_ON_START", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")

    def _not_ready(app_state):
        app_state.embedding_ready = False
        return False

    monkeypatch.setattr("services.embedding_readiness.try_activate_embedding", _not_ready)
    monkeypatch.setattr("services.index_bootstrap._file_index_count", lambda: 3)

    with patch("services.ingest.enqueue_initial_ingest_scan", return_value=True) as enqueue:
        with TestClient(main.app):
            pass

    enqueue.assert_not_called()


def test_lifespan_defer_skips_resync_when_resync_flag_absent(monkeypatch) -> None:
    """Lifespan does NOT enqueue resync when LUMOGIS_LIBRARY_RESYNC_ON_START is unset (LUM-478).

    Even with a prior index and a ready embedder, the resync gate requires the
    explicit opt-in flag. This preserves operator control over when cold-start
    rescans activate.
    """
    monkeypatch.setenv("LUMOGIS_DEFER_LIBRARY_INDEX", "1")
    monkeypatch.delenv("LUMOGIS_LIBRARY_RESYNC_ON_START", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _patch_embedding_ready(monkeypatch)
    monkeypatch.setattr("services.index_bootstrap._file_index_count", lambda: 3)

    with patch("services.ingest.enqueue_initial_ingest_scan", return_value=True) as enqueue:
        with TestClient(main.app):
            pass

    enqueue.assert_not_called()


def test_embedding_readiness_retry_enqueues_resync_when_prior_index(monkeypatch) -> None:
    """Retry job enqueues resync when it finds a ready embedder + prior index (LUM-478).

    This is ADR-096 branch #3: Core started with the embedder cold, the retry
    scheduler job fires 15 s later, finds the embedder now warm, and must enqueue
    the resync scan via the same defer+prior-index gate.

    Strategy: wrap scheduler.add_job *before* the lifespan runs so the retry
    closure is captured the moment it is registered, then invoke it directly.
    """
    import main as _main

    import config as _config

    monkeypatch.setenv("LUMOGIS_DEFER_LIBRARY_INDEX", "1")
    monkeypatch.setenv("LUMOGIS_LIBRARY_RESYNC_ON_START", "1")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr("services.index_bootstrap._file_index_count", lambda: 3)

    not_ready_calls = {"n": 0}

    def _not_ready_then_ready(app_state):
        not_ready_calls["n"] += 1
        # First call (lifespan startup): not ready → retry job registered.
        # Subsequent call (inside the retry closure): ready → resync enqueued.
        ready = not_ready_calls["n"] > 1
        app_state.embedding_ready = ready
        return ready

    monkeypatch.setattr(
        "services.embedding_readiness.try_activate_embedding",
        _not_ready_then_ready,
    )

    # Pre-fetch the scheduler singleton and intercept add_job so we capture
    # the retry closure as it is registered during lifespan startup.
    scheduler = _config.get_scheduler()
    captured: dict = {}
    _real_add_job = scheduler.add_job

    def _spy_add_job(fn, *args, **kwargs):
        if kwargs.get("id") == "embedding_readiness_retry":
            captured["fn"] = fn
        return _real_add_job(fn, *args, **kwargs)

    scheduler.add_job = _spy_add_job

    try:
        with patch("services.ingest.enqueue_initial_ingest_scan", return_value=True) as enqueue:
            with TestClient(_main.app):
                pass  # lifespan startup registers the job; closure is captured

            assert "fn" in captured, "retry job should have been registered when embedder not ready"
            # Invoke the closure inside the patch context so the late-binding
            # `from services.ingest import enqueue_initial_ingest_scan` inside
            # the closure still resolves to the mock.
            captured["fn"]()
            enqueue.assert_called_once()
    finally:
        scheduler.add_job = _real_add_job  # restore unconditionally
