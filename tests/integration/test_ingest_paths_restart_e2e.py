# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-400 — live-stack proof that POST /settings/restart reloads ingest_paths (opt-in)."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from restart_e2e_env import (
    assert_test_env_example_guard,
    backup_project_env,
    ensure_host_dir_writable,
    orchestrator_container_marker,
    reset_ingest_settings,
    restore_project_env,
    rewrite_env_key,
)

pytestmark = [pytest.mark.integration, pytest.mark.restart_e2e]

REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGIN = os.environ.get("LUMOGIS_PUBLIC_ORIGIN", "http://127.0.0.1").strip().rstrip("/") or "http://127.0.0.1"
BASE_URL = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000").strip().rstrip("/")
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "lumogis-test").strip() or "lumogis-test"
# Post-restart readiness budget. The restart_e2e Makefile target sets OLLAMA_SKIP_WAIT=true
# so the orchestrator binds immediately on --force-recreate (no on-boot Ollama wait/model
# pull — see orchestrator/docker-entrypoint.sh); 180s comfortably covers DB migrations + app init.
READY_TIMEOUT_S = 180
POLL_INTERVAL_S = 2
HEALTHZ_TIMEOUT_S = 60

_MODULE_ENV_BACKUP = backup_project_env(REPO_ROOT)


def _smoke_credentials() -> tuple[str, str]:
    email = os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL", "").strip()
    password = os.environ.get("LUMOGIS_WEB_SMOKE_PASSWORD", "")
    return email, password


@contextmanager
def _api_client() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE_URL, timeout=180.0) as client:
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                client.get("/health")
                last_exc = None
                break
            except (httpx.ConnectError, httpx.ReadError) as exc:
                last_exc = exc
                time.sleep(3)
        if last_exc is not None:
            pytest.skip(
                f"Orchestrator unreachable at {BASE_URL} after 5 attempts: {last_exc}"
            )
        yield client


def _admin_bearer(client: httpx.Client) -> str:
    email, password = _smoke_credentials()
    if not email or len(password) < 12:
        pytest.skip("LUMOGIS_WEB_SMOKE_EMAIL / LUMOGIS_WEB_SMOKE_PASSWORD unset or too short")
    lr = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": ORIGIN},
    )
    if lr.status_code == 503:
        pytest.skip("AUTH_ENABLED=false — login unavailable")
    assert lr.status_code == 200, lr.text[:800]
    token = lr.json().get("access_token")
    assert token
    return token


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_bearer(client)}", "Origin": ORIGIN}


def _poll_until(predicate, *, timeout_s: int, desc: str) -> None:
    deadline = time.monotonic() + timeout_s
    last_err = ""
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001 — poll helper
            last_err = str(exc)
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(f"timed out waiting for {desc} ({last_err})")


def _poll_health_ready(client: httpx.Client) -> None:
    def ready() -> bool:
        r = client.get("/healthz")
        return r.status_code == 200

    _poll_until(ready, timeout_s=READY_TIMEOUT_S, desc="GET /healthz HTTP 200")


def _poll_restart_required_clear(client: httpx.Client, headers: dict[str, str]) -> dict:
    last: dict = {}

    def cleared() -> bool:
        nonlocal last
        r = client.get("/settings", headers=headers)
        if r.status_code != 200:
            return False
        last = r.json()
        return last.get("restart_required") is False

    _poll_until(cleared, timeout_s=READY_TIMEOUT_S, desc="restart_required false")
    return last


def _poll_healthz_ingest_ok(client: httpx.Client) -> None:
    def ok() -> bool:
        r = client.get("/healthz")
        if r.status_code != 200:
            return False
        body = r.json()
        if body.get("ingest_paths_watch") != "ok":
            return False
        try:
            return int(body.get("ingest_paths_watch_roots", "0")) >= 1
        except (TypeError, ValueError):
            return False

    _poll_until(ok, timeout_s=HEALTHZ_TIMEOUT_S, desc="ingest_paths_watch ok")


def _post_restart(client: httpx.Client, headers: dict[str, str]) -> dict | None:
    try:
        r = client.post("/settings/restart", headers=headers, timeout=120.0)
    except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError):
        return None
    if r.status_code == 502:
        pytest.fail(f"POST /settings/restart 502: {r.text[:500]}")
    if r.status_code == 200:
        return r.json()
    return None


def _fallback_host_data_dir(repo_root: Path) -> Path:
    return (repo_root / "lumogis-data").resolve()


@pytest.fixture(autouse=True)
def _env_hygiene_per_test():
    restore_project_env(_MODULE_ENV_BACKUP, repo_root=REPO_ROOT)
    # The Postgres volume persists app_settings across `down`; clear any stored ingest-path
    # override so the DB value never masks the env fallback under test (cross-run isolation).
    reset_ingest_settings(compose_project=COMPOSE_PROJECT)
    assert_test_env_example_guard(REPO_ROOT)
    yield
    restore_project_env(_MODULE_ENV_BACKUP, repo_root=REPO_ROOT)
    reset_ingest_settings(compose_project=COMPOSE_PROJECT)


def test_restart_e2e_settings_restart_requires_admin():
    with _api_client() as client:
        r = client.post("/settings/restart")
    assert r.status_code == 401


def test_restart_e2e_1_malformed_ingest_paths_falls_back_after_restart():
    """Invalid INGEST_PATHS JSON in .env falls back to FILESYSTEM_ROOT_HOST after recreate."""
    restore_project_env(_MODULE_ENV_BACKUP, repo_root=REPO_ROOT)
    env_path = REPO_ROOT / ".env"
    assert env_path.is_file(), "repo-root .env required for restart E2E"

    with _api_client() as client:
        headers = _admin_headers(client)
        _poll_health_ready(client)
        _poll_healthz_ingest_ok(client)
        time.sleep(2)
        hr0 = client.get("/health", headers=headers)
        assert hr0.status_code == 200
        baseline_count = int(hr0.json().get("file_index_count", 0))
        settings_before = client.get("/settings", headers=headers).json()

        marker_before = orchestrator_container_marker(REPO_ROOT, compose_project=COMPOSE_PROJECT)

        content = env_path.read_text(encoding="utf-8")
        content = rewrite_env_key(content, "INGEST_PATHS", "not-json")
        env_path.write_text(content, encoding="utf-8")

        _post_restart(client, headers)
        _poll_health_ready(client)

        marker_after = orchestrator_container_marker(REPO_ROOT, compose_project=COMPOSE_PROJECT)
        assert marker_after != marker_before, "orchestrator was not recreated"

        settings_r = client.get("/settings", headers=headers)
        assert settings_r.status_code == 200
        body = settings_r.json()
        fallback_dir = _fallback_host_data_dir(REPO_ROOT)
        effective = body.get("ingest_paths") or []
        assert effective, "expected fallback ingest_paths after malformed JSON"
        resolved_fallback = str(fallback_dir)
        # ingest_paths may be reported as a relative host path (e.g. the compose default
        # "./lumogis-data"). Such paths are relative to the compose project dir (REPO_ROOT),
        # not pytest's cwd ($REPO_ROOT/orchestrator), so resolve against REPO_ROOT. Absolute
        # paths are unaffected (pathlib drops the left operand when the right is absolute).
        assert any(
            (REPO_ROOT / Path(p)).resolve() == fallback_dir for p in effective
        ), f"ingest_paths {effective!r} should include fallback {resolved_fallback!r}"

        _poll_healthz_ingest_ok(client)
        token = f"lgmalform_{uuid.uuid4().hex[:12]}"
        probe = fallback_dir / f"{token}.txt"
        ensure_host_dir_writable(fallback_dir)
        probe.write_text(f"malformed fallback probe {token}\n", encoding="utf-8")
        try:
            found = False
            for _ in range(60):
                time.sleep(POLL_INTERVAL_S)
                hr = client.get("/health", headers=headers)
                if hr.status_code == 200 and int(hr.json().get("file_index_count", 0)) > baseline_count:
                    found = True
                    break
            assert found, (
                f"file_index_count should increase on fallback root "
                f"(baseline={baseline_count}, now={client.get('/health', headers=headers).json().get('file_index_count')})"
            )
        finally:
            probe.unlink(missing_ok=True)

        _ = settings_before  # baseline captured for debugging if assertion fails


def test_restart_e2e_2_ingest_paths_restart_applies_pending_path():
    """PUT ingest_paths + POST /settings/restart remounts subdir at /data and ingests via watcher."""
    restore_project_env(_MODULE_ENV_BACKUP, repo_root=REPO_ROOT)

    subdir = (REPO_ROOT / f"lumogis-restart-e2e-{uuid.uuid4().hex[:12]}").resolve()
    subdir.mkdir(parents=True, exist_ok=True)
    token = f"lgrestart_{uuid.uuid4().hex[:12]}"
    probe = subdir / f"{token}.txt"
    probe.write_text(f"restart e2e probe {token}\n", encoding="utf-8")

    try:
        with _api_client() as client:
            headers = _admin_headers(client)
            marker_before = orchestrator_container_marker(REPO_ROOT, compose_project=COMPOSE_PROJECT)

            hr_pre = client.get("/health", headers=headers)
            assert hr_pre.status_code == 200
            count_before_restart = int(hr_pre.json().get("file_index_count", 0))

            r_put = client.put(
                "/settings",
                headers=headers,
                json={"ingest_paths": [str(subdir)]},
            )
            assert r_put.status_code == 200, r_put.text[:800]
            after_put = r_put.json()
            assert after_put.get("restart_required") is True
            assert after_put.get("pending_ingest_paths") == [str(subdir)]

            restart_body = _post_restart(client, headers)
            if restart_body is not None:
                assert restart_body.get("root_changed") is True

            # Wait for the force-recreate to finish before sampling the container marker.
            # _post_restart returns as soon as the old container drops the connection, which is
            # *before* the replacement container is up — sampling the marker now would race the
            # recreate and can still observe the old container id (`compose ps -q`).
            _poll_health_ready(client)
            marker_after = orchestrator_container_marker(REPO_ROOT, compose_project=COMPOSE_PROJECT)
            assert marker_after != marker_before, "orchestrator was not recreated"

            settled = _poll_restart_required_clear(client, headers)
            assert settled.get("ingest_paths") == [str(subdir)]

            _poll_healthz_ingest_ok(client)
            time.sleep(2)

            found = False
            for _ in range(60):
                time.sleep(POLL_INTERVAL_S)
                hr = client.get("/health", headers=headers)
                if hr.status_code == 200 and int(hr.json().get("file_index_count", 0)) > count_before_restart:
                    found = True
                    break
            assert found, (
                f"file_index_count should increase after restart ingest "
                f"(before_restart={count_before_restart}, token={token}, "
                f"now={client.get('/health', headers=headers).json().get('file_index_count')})"
            )
    finally:
        probe.unlink(missing_ok=True)
        subdir.rmdir()
        restore_project_env(_MODULE_ENV_BACKUP, repo_root=REPO_ROOT)
