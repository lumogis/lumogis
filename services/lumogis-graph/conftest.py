# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared test fixtures for the standalone lumogis-graph service.

Why this exists at the service root (not under `tests/`):
  pytest auto-discovers `conftest.py` walking up from the test file. Putting
  it here means `tests/test_*.py` can `from conftest import ...` AND every
  test inherits the same in-memory mocks for `config._instances`.

Mocks installed:
  * `MockMetadataStore` and `MockGraphStore` replace the Postgres / FalkorDB
    adapters so unit tests never touch a real network.
  * `MockScheduler` replaces APScheduler so we don't need the real package
    when running `make test-kg` from a thin local venv (the production
    image ships APScheduler; tests must run in either environment).
  * `apscheduler` is stubbed out at import time the same way Core's
    `orchestrator/tests/conftest.py` does it, so `import config` and
    `import graph` don't blow up if the host venv lacks the package.

Env defaults:
  * `GRAPH_BACKEND=falkordb` so `main.py:_hard_fail_if_no_falkordb` doesn't
    `sys.exit(1)` during lifespan-driven tests.
  * `KG_ALLOW_INSECURE_WEBHOOKS=true` and `GRAPH_WEBHOOK_SECRET` UNSET as
    the safe-by-default for tests that aren't specifically about auth —
    individual auth tests override both.
  * `KG_SCHEDULER_ENABLED=false` so the lifespan's auto-registration of
    daily/weekly jobs is skipped unless a test explicitly opts in.

NOTE: never set FALKORDB_URL / POSTGRES_URL here — the mocks short-circuit
those code paths. Setting them would risk a real connection if a test
forgets to override `_instances`.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. Ensure the service root is importable as a top-level package set
#    (`config`, `auth`, `routes`, `graph`, `models`, `kg_mcp`, `webhook_queue`,
#    `__version__`). Mirrors `Dockerfile`'s WORKDIR=/app convention.
# ---------------------------------------------------------------------------

_SERVICE_ROOT = Path(__file__).resolve().parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


# ---------------------------------------------------------------------------
# 2. Stub `apscheduler` if the host venv doesn't have it. The production
#    Docker image installs the real package; this stub keeps `make test-kg`
#    runnable from a slimmed-down local venv.
# ---------------------------------------------------------------------------


def _install_apscheduler_stub() -> None:
    if "apscheduler" in sys.modules:
        return
    aps_pkg = types.ModuleType("apscheduler")
    aps_schedulers = types.ModuleType("apscheduler.schedulers")
    aps_bg = types.ModuleType("apscheduler.schedulers.background")
    aps_triggers = types.ModuleType("apscheduler.triggers")
    aps_cron = types.ModuleType("apscheduler.triggers.cron")
    aps_interval = types.ModuleType("apscheduler.triggers.interval")

    class _StubBGScheduler:
        running = False

        def __init__(self, **kwargs):
            self._jobs: list = []

        def start(self) -> None:
            self.running = True

        def shutdown(self, wait: bool = True) -> None:
            self.running = False

        def add_job(self, *args, **kwargs):
            self._jobs.append((args, kwargs))
            return None

        def get_job(self, job_id: str):
            return None

        def get_jobs(self) -> list:
            return list(self._jobs)

    class _StubCronTrigger:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _StubIntervalTrigger:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    aps_bg.BackgroundScheduler = _StubBGScheduler
    aps_cron.CronTrigger = _StubCronTrigger
    aps_interval.IntervalTrigger = _StubIntervalTrigger
    sys.modules["apscheduler"] = aps_pkg
    sys.modules["apscheduler.schedulers"] = aps_schedulers
    sys.modules["apscheduler.schedulers.background"] = aps_bg
    sys.modules["apscheduler.triggers"] = aps_triggers
    sys.modules["apscheduler.triggers.cron"] = aps_cron
    sys.modules["apscheduler.triggers.interval"] = aps_interval


_install_apscheduler_stub()


# ---------------------------------------------------------------------------
# 3. Default env. Individual tests override via monkeypatch.setenv.
# ---------------------------------------------------------------------------

os.environ.setdefault("GRAPH_BACKEND", "falkordb")
os.environ.setdefault("KG_ALLOW_INSECURE_WEBHOOKS", "true")
os.environ.setdefault("KG_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")  # quieter test output


# ---------------------------------------------------------------------------
# 4. Mock adapters
# ---------------------------------------------------------------------------


class MockMetadataStore:
    """In-memory MetadataStore stand-in.

    Honours the small subset of the real interface that KG routes touch:
    `ping`, `execute`, `fetch_one`, `fetch_all`, `close`. Tests can poke
    canned rows in via `_seed_fetch_one` / `_seed_fetch_all`.
    """

    def __init__(self):
        self._fetch_one_rows: list = []
        self._fetch_all_rows: list[list] = []
        self.executed: list[tuple[str, tuple | None]] = []

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.executed.append((query, params))

    def fetch_one(self, query: str, params: tuple | None = None):
        return self._fetch_one_rows.pop(0) if self._fetch_one_rows else None

    def fetch_all(self, query: str, params: tuple | None = None) -> list:
        return self._fetch_all_rows.pop(0) if self._fetch_all_rows else []

    def close(self) -> None:
        pass

    def _seed_fetch_one(self, row) -> None:
        self._fetch_one_rows.append(row)

    def _seed_fetch_all(self, rows: list) -> None:
        self._fetch_all_rows.append(rows)


class MockGraphStore:
    """In-memory FalkorDB stand-in. Returns canned rows per-Cypher-prefix."""

    def __init__(self):
        self._canned: dict[str, list[dict]] = {}
        self.queries: list[tuple[str, dict]] = []

    def ping(self) -> bool:
        return True

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        self.queries.append((cypher, params or {}))
        for prefix, rows in self._canned.items():
            if cypher.lstrip().startswith(prefix):
                return list(rows)
        return []

    def _seed(self, cypher_prefix: str, rows: list[dict]) -> None:
        self._canned[cypher_prefix] = rows

    def close(self) -> None:
        pass


class MockScheduler:
    """No-op APScheduler stand-in for the lifespan's `scheduler.start/shutdown`."""

    running: bool = False

    def __init__(self):
        self._jobs: list[dict] = []

    def start(self) -> None:
        self.running = True

    def shutdown(self, wait: bool = True) -> None:
        self.running = False

    def add_job(self, func, trigger=None, *, id=None, name=None, replace_existing=True, **kwargs):
        self._jobs.append({"id": id, "name": name, "func": func, "trigger": trigger})
        return None

    def get_job(self, job_id: str):
        for j in self._jobs:
            if j["id"] == job_id:
                return j
        return None

    def get_jobs(self) -> list:
        return list(self._jobs)


# ---------------------------------------------------------------------------
# 5. Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_metadata_store():
    return MockMetadataStore()


@pytest.fixture
def mock_graph_store():
    return MockGraphStore()


@pytest.fixture
def mock_scheduler():
    return MockScheduler()


@pytest.fixture(autouse=True)
def _override_config_singletons(mock_metadata_store, mock_graph_store, mock_scheduler):
    """Replace adapter / scheduler singletons with mocks for every test.

    Yields nothing; the cleanup at the end is what matters — it clears
    `_instances` so the next test's `get_metadata_store()` etc. return
    fresh mocks (autouse means each test gets its own MockX instances).
    """
    import config as _config

    _config._instances["metadata_store"] = mock_metadata_store
    _config._instances["graph_store"] = mock_graph_store
    _config._instances["scheduler"] = mock_scheduler
    _config._instances["vector_store"] = None
    _config._instances["embedder"] = None
    yield
    _config._instances.clear()


@pytest.fixture
def app_with_lifespan(monkeypatch):
    """Return a TestClient that has actually run lifespan startup.

    Most route tests don't need this — they import `routes.X.router`
    and use a bare `FastAPI()` with that router mounted. Use this
    fixture only for tests that need the scheduler / MCP / webhook
    queue actually initialised (e.g. `test_scheduler_safety.py`).
    """
    monkeypatch.setenv("GRAPH_BACKEND", "falkordb")
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)
