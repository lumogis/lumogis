# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared test fixtures: mock adapters, test config, test client."""

import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Structured logging defaults for the test process (chunk:
# structured_audit_logging — plan D10). Set BEFORE any orchestrator
# import so that ``main.py``'s top-of-module ``configure_logging()``
# call (if pulled in transitively by a test) sees a known-good
# configuration. ``setdefault`` so a CI run that wants to assert JSON
# output can still override.
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("LOG_LEVEL", "DEBUG")

# No `.env` file reaches the unit lane (LUM-905). `main.py` calls a bare
# `load_dotenv()`, which walks up from `orchestrator/` through every parent
# folder until it finds a `.env`. In the main checkout that is the operator's
# live file; in a worktree under `.claude/worktrees/` it is the *same* file,
# three folders up, which the LUM-804 scrub list below does not cover (its
# relative `FILESYSTEM_ROOT` broke `test_browse_requires_admin`). Rather than
# chase each variable, stop the file being read at all: python-dotenv >= 1.2
# makes every `load_dotenv()` a no-op while this is set. Assigned, not
# `setdefault`, so an inherited `PYTHON_DOTENV_DISABLED=0` cannot re-enable it.
# Set at import, before anything can import `main`. Production is unaffected:
# Docker images exclude `.env` (`.dockerignore`) and get env from compose, and a
# native `uvicorn main:app` never loads this conftest.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

# Stub out apscheduler so that plugins/graph/__init__.py can be imported
# in unit tests without the full production dependency being installed.
# The scheduler itself is replaced by MockScheduler in the _override_config fixture.
if "apscheduler" not in sys.modules:
    _aps_pkg = types.ModuleType("apscheduler")
    _aps_schedulers = types.ModuleType("apscheduler.schedulers")
    _aps_bg = types.ModuleType("apscheduler.schedulers.background")

    class _MockBGScheduler:
        running = False

        def __init__(self, **kwargs):
            pass

        def start(self):
            self.running = True

        def shutdown(self, wait=True):
            self.running = False

        def add_job(self, *a, **kw):
            return None

        def get_job(self, job_id):
            return None

        def get_jobs(self):
            return []

    _aps_bg.BackgroundScheduler = _MockBGScheduler
    sys.modules["apscheduler"] = _aps_pkg
    sys.modules["apscheduler.schedulers"] = _aps_schedulers
    sys.modules["apscheduler.schedulers.background"] = _aps_bg

# SDK packages that are real dependencies (requirements-core.txt) and that
# some tests import for real (e.g. `openai.APIConnectionError` in
# test_egress_guard.py). A test module that plants a stub for one of these in
# `sys.modules` at import time poisons every test collected after it, and the
# damage depends on collection order (LUM-944). Stub them per test with
# `monkeypatch.setitem(sys.modules, ...)`, which undoes itself.
_REAL_SDK_MODULES = ("openai", "anthropic")


def _leaked_sdk_stubs() -> list[str]:
    """Return the SDK names whose `sys.modules` entry is not a real package."""
    leaked = []
    for name in _REAL_SDK_MODULES:
        if name in sys.modules and getattr(sys.modules[name], "__file__", None) is None:
            leaked.append(name)
    return leaked


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    """Fail collection of the module that leaves a stub SDK in `sys.modules`.

    Importing a test module happens during its collection, so this checks
    right after each module is collected and blames that module. The stub is
    then dropped so the rest of the run still sees the real package.
    """
    report = yield
    if isinstance(collector, pytest.Module):
        leaked = _leaked_sdk_stubs()
        if leaked:
            for name in leaked:
                sys.modules.pop(name, None)
            report.outcome = "failed"
            report.longrepr = (
                f"{collector.nodeid} left a stub {', '.join(leaked)} module in "
                "sys.modules at import time. It would leak into every later test. "
                "Install it per test with monkeypatch.setitem(sys.modules, ...) (LUM-944)."
            )
    return report


@pytest.fixture(autouse=True, scope="module")
def _assert_real_sdk_modules():
    """Fail a module whose tests leave a stub SDK in `sys.modules` behind."""
    yield
    leaked = _leaked_sdk_stubs()
    if leaked:
        for name in leaked:
            sys.modules.pop(name, None)
        pytest.fail(
            f"a test in this module left a stub {', '.join(leaked)} module in sys.modules; "
            "use monkeypatch.setitem(sys.modules, ...) (LUM-944)",
            pytrace=False,
        )


# Point config loaders to the actual files in config/ (project root).
# Two layouts to support:
#   * host checkout: __file__ = .../orchestrator/tests/conftest.py → repo/config
#   * Docker image:  __file__ = /app/tests/conftest.py and config/ is mounted
#                    at /opt/lumogis/config/ (see orchestrator/Dockerfile).
# We pick the first candidate that actually exists; if none do, we fall back
# to the host-checkout path so a missing-fixture failure surfaces with a
# meaningful path in the error message rather than silently returning [].
_CONFIG_CANDIDATES = [
    Path(__file__).parent.parent.parent / "config",
    Path("/opt/lumogis/config"),
]
_CONFIG_DIR = next(
    (p for p in _CONFIG_CANDIDATES if (p / "ollama_catalog_fallback.json").is_file()),
    _CONFIG_CANDIDATES[0],
)
os.environ.setdefault("MODELS_CONFIG", str(_CONFIG_DIR / "models.yaml"))
os.environ.setdefault("OLLAMA_CATALOG_FALLBACK", str(_CONFIG_DIR / "ollama_catalog_fallback.json"))

# Unit tests assume the legacy in-process graph plugin layout and an empty
# capability registry by default. When the test container inherits the host
# stack's runtime env (`GRAPH_MODE=service`, `CAPABILITY_SERVICE_URLS=...`),
# `plugins/graph/__init__.py` short-circuits to `router = None` (no
# `/graph/backfill` route) and the lifespan auto-registers the live
# `lumogis-graph` service in the registry — both break tests that expect a
# clean default. Unset them here at module-load time, BEFORE any test module
# imports `plugins.graph.*` or `services.capability_registry`. Tests that
# specifically need a non-default value set it via `monkeypatch.setenv`.
for _stack_only_env in ("GRAPH_MODE", "CAPABILITY_SERVICE_URLS"):
    os.environ.pop(_stack_only_env, None)

# Test doubles live in tests/_fakes.py so they can be imported without the heavy
# conftest import chain; re-exported here for back-compat (test_entities,
# test_adapters, test_recall_legs import these names).
from tests._fakes import MockVectorStore  # noqa: E402,F401
from tests._fakes import _match_clause  # noqa: E402,F401
from tests._fakes import _matches_qdrant_filter  # noqa: E402,F401

import config as _config  # noqa: E402 — after env/path bootstrap; see module docstring above.


def graph_store_cache_key(bank: str = "personal") -> str:
    """Bank-scoped graph store singleton key (LUM-293) for test injection."""
    return _config._graph_store_cache_key(bank)


def set_test_graph_store(gs) -> None:
    """Inject a mock GraphStore for parameterless ``get_graph_store()`` paths."""
    _config._instances[graph_store_cache_key()] = gs


def disable_test_graph_store() -> None:
    """Disable graph store for tests that expect projection no-ops."""
    _config._instances[graph_store_cache_key()] = None


class MockMetadataStore:
    def __init__(self):
        self._data: list[dict] = []

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        pass

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = (query or "").lower()
        if "allows_shared" in q:
            return {"allows_shared": True}
        if "app_settings" in q and params:
            key = params[0]
            if key == "privacy_mode":
                return {"value": "allow_cloud"}
        if "privacy_user_settings" in q:
            return None
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        return []

    def close(self) -> None:
        pass

    @contextmanager
    def transaction(self):
        yield

    @contextmanager
    def isolated_transaction(self):
        """Yield ``self`` as the bound handle (LUM-65).

        Production runs this on a dedicated connection; a mock has none, so this
        models the calling shape only. The isolation itself is proved against a
        real Postgres in ``tests/integration/test_postgres_store_concurrency_live.py``.
        """
        yield self


class MockEmbedder:
    def ping(self) -> bool:
        return True

    @property
    def vector_size(self) -> int:
        return 768

    def embed(self, text: str) -> list[float]:
        return [0.0] * 768

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 768 for _ in texts]


class MockScheduler:
    """No-op APScheduler stand-in for unit tests.

    Supports the full scheduler API used by main.py and signal monitors:
    start(), shutdown(), running, add_job(), get_job(), get_jobs().
    """

    running: bool = False

    def start(self) -> None:
        self.running = True

    def shutdown(self, wait: bool = True) -> None:
        self.running = False

    def add_job(self, *args, **kwargs):
        return None

    def get_job(self, job_id: str):
        return None

    def get_jobs(self) -> list:
        return []


@pytest.fixture
def mock_vector_store():
    return MockVectorStore()


@pytest.fixture
def mock_metadata_store():
    return MockMetadataStore()


@pytest.fixture
def mock_embedder():
    return MockEmbedder()


@pytest.fixture
def mock_scheduler():
    return MockScheduler()


#: Operator/stack environment that must NOT reach the unit lane (LUM-804).
#:
#: `orchestrator/main.py` calls `load_dotenv()`, so running pytest from the repo
#: root loads the real `.env` — the operator's live configuration. The suite then
#: exercises whatever that file says rather than the product CI ships, and the
#: verdict depends on **where it was launched from**: the same commit and command
#: gave 296 failures from the repo root and 0 in a git worktree, where `.env` is
#: absent because it is gitignored.
#:
#: `AUTH_ENABLED` is the one that surfaced it — `.env` sets it to `true`, and 296
#: tests expecting `200` got `401`. It failed loudly, which was luck; a variable
#: whose wrong value does not happen to break an assertion changes behaviour
#: silently and the suite still reports green.
#:
#: `LUMOGIS_PUBLIC_ORIGIN` is the quieter twin. CSRF `require_same_origin` runs
#: *before* `require_user`. A TestClient POST with no Origin header then
#: returns **403 origin mismatch** instead of **401 unauthenticated**
#: (`test_source_create.py::test_an_unauthenticated_caller_is_401`). Isolation
#: still passed; the full `make test` lane did not. Tests that pin an origin
#: already `monkeypatch.setenv` after this fixture.
#:
#: Listing them here rather than deleting them inline makes the policy explicit:
#: **this is what the unit lane is allowed to ignore about your machine.** Tests
#: that genuinely want one set it via `monkeypatch.setenv` after this autouse
#: fixture runs (74 modules already do this for `AUTH_ENABLED`), so neutralising
#: it here changes nothing for them. Unset, `auth.auth_enabled()` reads `false`.
#:
#: Add to this list when a new operator variable is found to steer the unit lane.
_NEUTRALISED_OPERATOR_ENV = (
    "GRAPH_MODE",
    "CAPABILITY_SERVICE_URLS",
    "AUTH_ENABLED",
    "LUMOGIS_PUBLIC_ORIGIN",
)


@pytest.fixture(autouse=True)
def _override_config(
    mock_vector_store, mock_metadata_store, mock_embedder, mock_scheduler, monkeypatch
):
    """Replace config singletons with mocks for every test.

    Also sets RERANKER_BACKEND=none so the lifespan never tries to import
    sentence_transformers (which is only available inside the Docker image).
    APScheduler is mocked via MockScheduler so the local venv does not need
    the apscheduler package — it is only available inside the Docker image.
    """
    monkeypatch.setenv("RERANKER_BACKEND", "none")
    # Test defaults: tests assume the legacy in-process graph plugin and an
    # empty capability registry. The test container inherits the live stack's
    # env (`GRAPH_MODE=service`, `CAPABILITY_SERVICE_URLS=...`) which would
    # otherwise (a) self-disable `plugins/graph/` (no `/graph/backfill`
    # route) and (b) trigger lifespan discovery of the live `lumogis-graph`
    # service, polluting registry-counting assertions. Tests that specifically
    # need a non-default value set it via `monkeypatch.setenv` after this
    # autouse fixture runs.
    for _var in _NEUTRALISED_OPERATOR_ENV:
        monkeypatch.delenv(_var, raising=False)
    # Bypass main._enforce_auth_consistency for tests that exercise
    # AUTH_ENABLED=true without seeding a real admin row. The variable
    # name is intentionally long and ugly so it cannot accidentally
    # appear in production code. Phase 1 / Phase 2 tests that *want*
    # the gate flip it back to "false" via the `no_skip_consistency`
    # fixture in tests/test_auth_phase1.py.
    monkeypatch.setenv(
        "_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION",
        "true",
    )
    # LUM-885: the refresh secret no longer falls back to AUTH_SECRET, so every
    # test that logs in with only AUTH_SECRET set needs a distinct refresh
    # secret. Tests that need it unset call ``delenv`` after this fixture.
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "unit-lane-refresh-secret-distinct-from-auth")
    _config._instances["vector_store"] = mock_vector_store
    _config._instances["metadata_store"] = mock_metadata_store
    _config._instances["embedder"] = mock_embedder
    _config._instances["reranker"] = None
    _config._instances["scheduler"] = mock_scheduler
    _config.set_effective_graph_mode_for_process(None)
    _config.clear_graph_mode_env_cache()
    yield
    _config._instances.clear()
    _config.reset_notification_factories()
    _config.set_effective_graph_mode_for_process(None)
    _config.clear_graph_mode_env_cache()
    _config._graph_store_import_warning_emitted = False


@pytest.fixture(autouse=True)
def _logging_reset():
    """Reset structlog + stdlib logging to a known baseline per test.

    Plan ``structured_audit_logging`` D10 — explicit pytest fixture
    instead of production-code branches that detect pytest. Tests that
    capture structured output via ``structlog.testing.capture_logs()``
    or that bind their own contextvars get a clean slate; tests that
    rely on ``caplog`` see the stdlib bridge re-attached fresh on every
    function.
    """
    from logging_config import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()


from services import ingest as _ingest_for_watcher_mocks  # noqa: E402

_REAL_START_WATCHER = _ingest_for_watcher_mocks.start_watcher
_REAL_STOP_WATCHER = _ingest_for_watcher_mocks.stop_watcher
_REAL_START_INGEST_PATH_WATCHERS = _ingest_for_watcher_mocks.start_ingest_path_watchers
_REAL_STOP_INGEST_PATH_WATCHERS = _ingest_for_watcher_mocks.stop_ingest_path_watchers


@pytest.fixture
def real_filesystem_watchers(monkeypatch):
    """Re-enable real watchdog start/stop (autouse mocks disable them by default)."""
    monkeypatch.setattr("services.ingest.start_watcher", _REAL_START_WATCHER)
    monkeypatch.setattr("services.ingest.stop_watcher", _REAL_STOP_WATCHER)
    monkeypatch.setattr(
        "services.ingest.start_ingest_path_watchers", _REAL_START_INGEST_PATH_WATCHERS
    )
    monkeypatch.setattr(
        "services.ingest.stop_ingest_path_watchers", _REAL_STOP_INGEST_PATH_WATCHERS
    )


@pytest.fixture(autouse=True)
def _reset_oauth_refresh_backoff(request, monkeypatch):
    """LUM-761: the refresh negative cache is process state keyed by mailbox+token
    digest, and OAuth test modules reuse one mailbox and token across tests — without
    this, one test's `network` failure turns the next test's assertion into a cached hit.
    Global rather than per-module (unlike update_check's / email_organize's caches)
    because any test reaching the real get_access_token, including via load_connection,
    writes it. The jittered retry pause is a real sleep; it is no-oped unless the test
    requests `real_oauth_retry_pause`."""
    from services import email_oauth  # lazy: no import at collection time

    email_oauth._reset_refresh_backoff()
    if "real_oauth_retry_pause" not in request.fixturenames:
        monkeypatch.setattr(email_oauth, "_retry_pause", lambda: None)
    yield
    email_oauth._reset_refresh_backoff()


@pytest.fixture
def real_oauth_retry_pause():
    """Opt-in marker: keep email_oauth._retry_pause real (see the autouse fixture above)."""


@pytest.fixture(autouse=True)
def _mock_watcher(monkeypatch):
    """Prevent the filesystem watcher from starting during tests.

    start_watcher uses watchdog to monitor a real path; that path does not
    exist in the local test environment (it lives inside Docker).
    """
    monkeypatch.setattr("services.ingest.start_watcher", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.ingest.stop_watcher", lambda: None)
    monkeypatch.setattr("services.ingest.start_ingest_path_watchers", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.ingest.stop_ingest_path_watchers", lambda: None)
    monkeypatch.setattr("services.ingest.schedule_inbox_poll", lambda: None)
    monkeypatch.setattr("services.ingest.unschedule_inbox_poll", lambda: None)


# Suites that exercise real ``batch_queue.enqueue`` or
# ``enqueue_initial_ingest_scan`` logic must opt out of the lifespan stub.
_BATCH_QUEUE_REAL_TEST_FILES = frozenset(
    {
        "test_batch_queue.py",
        "test_ingest_paths_watcher.py",
        # LUM-710: the unsubscribe action's whole job is now to enqueue, so a
        # stubbed enqueue would leave nothing to assert.
        "test_email_unsubscribe_batch.py",
    }
)


def _pytest_node_basename(request) -> str:
    node = request.node
    path = getattr(node, "path", None) or getattr(node, "fspath", None)
    if path is None:
        return ""
    return Path(path).name


@pytest.fixture(autouse=True)
def _stub_lifespan_batch_enqueue(monkeypatch, request):
    """Prevent TestClient lifespan from enqueueing ingest scans against fake stores.

    When the embedder mock reports ready, ``main.lifespan`` calls
    ``enqueue_initial_ingest_scan()`` which INSERTs into ``user_batch_jobs``.
    Route/integration fakes (``_RoutesFakeStore``, etc.) do not model that
    table, so the INSERT returns no id and startup fails. Real batch-queue
    behaviour is covered in ``test_batch_queue.py`` and ingest watcher tests.
    """
    if _pytest_node_basename(request) in _BATCH_QUEUE_REAL_TEST_FILES:
        return
    monkeypatch.setattr("services.ingest.enqueue_initial_ingest_scan", lambda: False)
    monkeypatch.setattr("services.batch_queue.enqueue", lambda **_kwargs: 1)


@pytest.fixture(autouse=True)
def _stub_lifespan_stop_entities_copy(monkeypatch, request):
    """``main.lifespan`` copies the stop-entities list into ``WORKSPACE_PATH`` (LUM-922).

    Under ``make compose-test`` that is the developer's real workspace. The copy
    itself is covered in ``test_stop_entities_store.py``.
    """
    if _pytest_node_basename(request) == "test_stop_entities_store.py":
        return
    monkeypatch.setattr("config.ensure_stop_entities_file", lambda: False)


@pytest.fixture(autouse=True)
def _stub_post_bootstrap_default_user_remap(monkeypatch):
    """``main.lifespan`` calls ``db_default_user_remap.main()`` which waits on Postgres."""
    monkeypatch.setattr("db_default_user_remap.main", lambda **_kwargs: 0)


@pytest.fixture(autouse=True)
def _lenient_tv_claim_for_synthetic_tokens(monkeypatch):
    """Accept test bearers that carry no ``tv`` claim.

    ``LUMOGIS_REQUIRE_TV_CLAIM`` defaults on since LUM-918, but most route tests
    mint bearers for synthetic users with ``mint_access_token(uid, role)`` or a
    hand-built payload, both without ``tv``. Those tests are about the route,
    not the gate, so the suite keeps the old lenient setting. A stale ``tv`` is
    still rejected. The default itself, and the production login and refresh
    tokens passing it, are covered in ``test_auth_token_defaults_lum918.py``,
    which removes this variable.
    """
    monkeypatch.setenv("LUMOGIS_REQUIRE_TV_CLAIM", "false")
