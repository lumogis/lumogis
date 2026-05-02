# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""
Lumogis orchestrator – FastAPI app.

App creation, lifespan (health checks, collection init, shutdown),
and router includes. All endpoint logic lives in routes/.
"""

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

# Structured logging bootstrap (chunk: structured_audit_logging). Must
# run BEFORE any other orchestrator import so that loggers acquired in
# downstream modules inherit the configured root handlers / level.
# Fail-fast: a bad LOG_FORMAT / LOG_LEVEL raises here and uvicorn
# refuses to start the app — see plan D11.
from logging_config import configure_logging  # noqa: E402

configure_logging()

import hooks
from auth import auth_middleware
from correlation import correlation_middleware
from fastapi import FastAPI
from plugins import load_plugins
from routes.actions import router as actions_router
from routes.admin import router as admin_router
from routes.admin_diagnostics import router as admin_diagnostics_router
from routes.admin_users import imports_router as admin_user_imports_router
from routes.admin_users import router as admin_users_router
from routes.auth import router as auth_router
from routes.capabilities import router as capabilities_router
from routes.chat import router as chat_router
from routes.data import router as data_router
from routes.events import register_hooks as register_sse_hooks
from routes.events import router as events_router
from routes.connector_credentials import admin_router as connector_credentials_admin_router
from routes.connector_credentials import (
    household_admin_router as connector_credentials_household_admin_router,
)
from routes.connector_credentials import router as connector_credentials_router
from routes.connector_credentials import (
    system_admin_router as connector_credentials_system_admin_router,
)
from routes.mcp_tokens import admin_router as mcp_tokens_admin_router
from routes.mcp_tokens import router as mcp_tokens_router
from routes.me import router as me_router
from routes.scope import router as scope_router
from routes.signals import router as signals_router
from routes.web import router as web_router

import config
import mcp_server

_log = logging.getLogger(__name__)

# Qdrant collections created on startup.
# "signals" stores embedded content summaries for semantic dedup.
# Vector size 768 matches Nomic Embed. Changing later requires drop + re-index.
_COLLECTIONS = ["documents", "conversations", "entities", "signals"]


def _wire_graph_mode_handlers(graph_mode: str) -> str:
    """Wire Core to the graph layer per `GRAPH_MODE`. Returns the resolved mode.

    Three modes select where graph projection / context lookup happens:

    * `inprocess` (default) — `plugins/graph/` runs inside Core, hooks fire
      in-process, weekly KG quality job is scheduled on Core's APScheduler
      (the caller checks `if scheduler and graph_mode == "inprocess":`
      to decide). Legacy behaviour.
    * `service` — `plugins/graph/` self-disables (mode guard in
      `plugins/graph/__init__.py`). Core dispatches every graph hook event
      over HTTP to the out-of-process `lumogis-graph` service via
      `services.graph_webhook_dispatcher`, the chat hot path calls KG
      `/context` for graph fragments, and the LLM tool registry exposes
      a `query_graph` proxy ToolSpec that POSTs to KG's
      `/tools/query_graph`. Core's APScheduler does NOT register the
      weekly KG quality job — the KG service runs that on its own
      scheduler.
    * `disabled` — No graph at all. The plugin self-disables, the
      dispatcher is not wired up, the chat path skips the `/context`
      call, and the weekly job is not registered. Use this when the
      operator opts out of the graph entirely.

    Extracted as a module-level function so `tests/test_main_lifespan_modes.py`
    can verify the branching without booting the full lifespan (which
    pulls Postgres, Qdrant, Ollama, the embedder and the watchdog —
    none of which are needed to exercise three IF branches).
    """
    if graph_mode == "service":
        from services.graph_webhook_dispatcher import register_core_callbacks
        from services.tools import register_query_graph_proxy

        register_core_callbacks()
        register_query_graph_proxy()
        _log.info("Graph mode: service — KG webhooks + /context proxy enabled")
    elif graph_mode == "disabled":
        _log.info("Graph mode: disabled — no graph functionality")
    else:
        _log.info("Graph mode: inprocess — graph plugin runs inside Core")
    return graph_mode


def _enforce_auth_consistency() -> None:
    """Refuse to boot when the env contradicts the users table.

    Lifespan ordering (per ADR ``family_lan_multi_user``):
      (a) migrations — applied by ``docker-entrypoint.sh`` BEFORE uvicorn.
      (b) DB ping — done by the lifespan above.
      (c) ``users.bootstrap_if_empty()`` — seed the bootstrap admin from env.
      (d) ``_enforce_auth_consistency()`` — this function. The gate MUST
          run after bootstrap so a fresh boot with bootstrap env set does
          not trigger the empty-table refusal.

    Two refusal conditions, both raising ``RuntimeError`` (which crashes
    the FastAPI startup and exits the container):

    * ``AUTH_ENABLED=false`` AND ``users`` has more than one row — the
      operator is in dev mode but has provisioned multiple accounts. We
      refuse so the wrong mental model doesn't silently succeed.
    * ``AUTH_ENABLED=true`` AND ``users`` is empty (bootstrap already ran
      and didn't create anyone — env unset or password too short) — the
      LAN would have no way in. Refuse with a remediation hint.
    """
    from auth import auth_enabled
    import services.users as users_svc

    # Test-only escape hatch — NEVER set in production, NEVER read from
    # any other module. Allows tests that exercise AUTH_ENABLED=true
    # (e.g. asserting a route returns 401) to boot without seeding a
    # real users-table row through the mocked metadata store. The
    # leading underscore + `_TEST_` segment + `_DO_NOT_SET_IN_PRODUCTION`
    # suffix flag the variable as private to the test harness; it lives
    # only in `orchestrator/tests/conftest.py` (autouse fixture).
    _test_skip = os.environ.get(
        "_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION", ""
    ).strip().lower()
    if _test_skip == "true":
        return

    n = users_svc.count_users()
    enabled = auth_enabled()
    if not enabled and n > 1:
        raise RuntimeError(
            "AUTH_ENABLED=false but users table has %d rows — refusing to boot. "
            "Either set AUTH_ENABLED=true (family-LAN mode) or reduce the table "
            "to a single user (single-user dev). See "
            ".cursor/plans/family_lan_multi_user.plan.md §2 D5." % n
        )
    if enabled and n == 0:
        raise RuntimeError(
            "AUTH_ENABLED=true but users table is empty and no bootstrap admin "
            "was created. Set LUMOGIS_BOOTSTRAP_ADMIN_EMAIL and "
            "LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD (>=12 chars) in .env and restart, "
            "or set AUTH_ENABLED=false for single-user dev."
        )

    if enabled:
        # AUTH_SECRET hardening (post-/verify-plan follow-up): the orchestrator
        # cannot mint or verify access JWTs without a real signing secret, so
        # refuse to boot rather than fail at first login. Empty, all-whitespace,
        # the example placeholder, or the legacy `__GENERATE_ME__` sentinel
        # (which never landed in the entrypoint's auto-rotation loop) all count
        # as "not set". See .cursor/plans/family_lan_multi_user.plan.md
        # Implementation Log → "Recommended next steps" #1.
        secret = os.environ.get("AUTH_SECRET", "").strip()
        if not secret or secret in ("change-me-in-production", "__GENERATE_ME__"):
            raise RuntimeError(
                "AUTH_ENABLED=true but AUTH_SECRET is unset or a placeholder — "
                "refusing to boot. Generate a real secret with "
                "`openssl rand -hex 32` (or `python3 -c \"import secrets; "
                "print(secrets.token_hex(32))\"`) and set it as AUTH_SECRET in "
                ".env, then restart. The entrypoint auto-rotates JWT_SECRET / "
                "JWT_REFRESH_SECRET / RESTART_SECRET but intentionally does NOT "
                "auto-rotate AUTH_SECRET — operators flip family-LAN mode on "
                "deliberately and must own the access-token signing secret."
            )

        # LUMOGIS_CREDENTIAL_KEY[S] hardening (per_user_connector_credentials
        # plan §Modified files → main.py): the per-user connector credential
        # service `services.connector_credentials._load_keys()` raises
        # `RuntimeError` when no usable Fernet key is configured, but that
        # error only surfaces on the first PUT/GET — a fresh boot would
        # otherwise come up "healthy" and silently 503 every credential
        # operation. Refuse to boot instead so misconfiguration is visible
        # at deploy time. Mirrors the AUTH_SECRET block above and the
        # `docker-entrypoint.sh` hardening block byte-for-byte.
        #
        # Honoured order matches `_load_keys()`:
        #   `LUMOGIS_CREDENTIAL_KEYS` (CSV, newest first) overrides
        #   `LUMOGIS_CREDENTIAL_KEY` (single key) when set; entrypoint uses
        #   `${LUMOGIS_CREDENTIAL_KEYS:-${LUMOGIS_CREDENTIAL_KEY:-}}`.
        # We do NOT validate Fernet-key shape here — that is `_load_keys()`'s
        # job at request time. We only catch the unset / placeholder case.
        # NOT auto-rotated by the entrypoint: losing this key makes every
        # encrypted credential row in `user_connector_credentials`
        # unrecoverable.
        cred_keys = (
            os.environ.get("LUMOGIS_CREDENTIAL_KEYS", "").strip()
            or os.environ.get("LUMOGIS_CREDENTIAL_KEY", "").strip()
        )
        if not cred_keys or cred_keys in ("change-me-in-production", "__GENERATE_ME__"):
            raise RuntimeError(
                "AUTH_ENABLED=true but LUMOGIS_CREDENTIAL_KEY[S] is unset or a "
                "placeholder — refusing to boot. Generate a real key with "
                "`python3 -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"` and set it as "
                "LUMOGIS_CREDENTIAL_KEY in .env (or LUMOGIS_CREDENTIAL_KEYS "
                "for a CSV during rotation), then restart. The entrypoint "
                "intentionally does NOT auto-rotate this key — losing it "
                "makes every per-user connector credential unrecoverable."
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log.info("Startup: pinging backends...")

    # Qdrant and Postgres are hard requirements — crash if unavailable.
    for name, getter in [
        ("vector_store", config.get_vector_store),
        ("metadata_store", config.get_metadata_store),
    ]:
        backend = getter()
        if not backend.ping():
            raise RuntimeError(
                f"STARTUP FAILED: {name} ({type(backend).__name__}) is unreachable. "
                f"Check the service and connection settings in .env."
            )
        _log.info("  %s (%s): OK", name, type(backend).__name__)

    # Auth bootstrap + consistency gate. Migrations have already been
    # applied by docker-entrypoint.sh before uvicorn started, so the
    # `users` table exists. Order is intentional: bootstrap creates the
    # admin from env (idempotent — no-op if any user exists), then the
    # gate refuses to boot when env contradicts the live table.
    try:
        from services.users import bootstrap_if_empty
        bootstrap_if_empty()
    except Exception:
        _log.exception("Bootstrap admin creation hit an unexpected error")
    _enforce_auth_consistency()
    # Plan llm_provider_keys_per_user_migration Pass 2.6: refuse to boot if
    # SIGNAL_LLM_MODEL is a cloud model under AUTH_ENABLED=true (no per-user
    # key resolution is possible for non-user-scoped signal sources). Local
    # default + auth-off both pass silently.
    config._check_background_model_defaults()

    # Embedder is a soft requirement — start in degraded mode if model unavailable.
    # ping() checks both Ollama liveness AND model presence (via /api/show).
    # False means "Ollama is down" OR "model not yet pulled" — log covers both.
    embedder = config.get_embedder()
    _embedding_ready = False
    if not embedder.ping():
        _log.warning(
            "Embedding model not ready at startup (Ollama unreachable or %s not pulled). "
            "Search and ingest will be unavailable. "
            "Open http://localhost:8000/dashboard → Settings → Models to pull the model, "
            "or check that the Ollama service is running.",
            os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
        )
    else:
        try:
            vs = config.get_vector_store()
            dim = embedder.vector_size
            for coll in _COLLECTIONS:
                vs.create_collection(coll, dim)
                # Payload indexes for the household visibility filter
                # (`(scope='personal' AND user_id=$me) OR scope IN ('shared','system')`).
                # Without these, every Qdrant search degrades to a full payload
                # scan once the household corpus grows. Both calls are idempotent.
                # See plan §4 (Qdrant pass) + visibility.visible_qdrant_filter.
                vs.ensure_payload_index(coll, "scope")
                vs.ensure_payload_index(coll, "user_id")
            _embedding_ready = True
            _log.info("Embedder vector size: %d — collections + payload indexes ready", dim)
        except Exception as exc:
            _log.warning(
                "Embedding model not ready (%s). "
                "Qdrant collections will be created when the model becomes available. "
                "Pull %s via http://localhost:8000/dashboard → Settings → Models.",
                exc,
                os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
            )

    app.state.embedding_ready = _embedding_ready

    try:
        reranker = config.get_reranker()
    except Exception as exc:
        _log.warning("Reranker failed to load (%s) — search will proceed without reranking", exc)
        config._instances["reranker"] = None
        reranker = None

    if reranker is not None and _embedding_ready:
        try:
            reranker.warmup()
        except Exception as exc:
            _log.warning("Reranker warmup failed (%s) — search will proceed without reranking", exc)
            config._instances["reranker"] = None
    elif reranker is not None:
        _log.info("Reranker warmup skipped — embedding not ready")
    else:
        _log.info("Reranker disabled (RERANKER_BACKEND=none)")

    from permissions import seed_defaults

    seed_defaults()

    from actions.rc_fixture_registry import register_rc_approval_fixture_actions_if_enabled

    register_rc_approval_fixture_actions_if_enabled()

    plugin_routers = load_plugins()
    for plugin_router in plugin_routers:
        app.include_router(plugin_router)

    from services.ingest import start_watcher

    start_watcher("/workspace/inbox")

    # APScheduler: start scheduler then load signal sources.
    scheduler = config.get_scheduler()
    scheduler.start()
    _log.info("APScheduler started")

    from signals import start_all as start_signal_monitors

    start_signal_monitors()

    # Register SSE hook callbacks and built-in routines.
    register_sse_hooks()

    from services.webpush import register_web_push_hooks

    register_web_push_hooks()

    from services.routines import register_all as register_routines

    register_routines()

    _batch_queue_enabled = os.environ.get("BATCH_QUEUE_ENABLED", "true").lower() not in (
        "false",
        "0",
        "no",
    )
    if scheduler and _batch_queue_enabled:
        from uuid import uuid4

        from services import batch_handlers as _batch_handlers_registered  # noqa: F401
        from services.batch_queue import BATCH_QUEUE_MAX_ATTEMPTS
        from services.batch_queue import BATCH_QUEUE_STUCK_AFTER_SECONDS
        from services.batch_queue import BATCH_QUEUE_STUCK_SWEEPER_SECONDS
        from services.batch_queue import BATCH_QUEUE_TICK_DRAIN_LIMIT
        from services.batch_queue import BATCH_QUEUE_TICK_SECONDS
        from services.batch_queue import reset_stuck
        from services.batch_queue import _run_one_tick

        _worker_id = f"orch-{os.getpid()}-{uuid4().hex[:8]}"

        def _batch_tick() -> None:
            try:
                for _ in range(BATCH_QUEUE_TICK_DRAIN_LIMIT):
                    if not _run_one_tick(_worker_id):
                        break
            except Exception:
                _log.exception("batch_queue_tick failed")

        def _batch_stuck_sweep() -> None:
            try:
                reset_stuck(
                    stuck_after_seconds=BATCH_QUEUE_STUCK_AFTER_SECONDS,
                    max_attempts=BATCH_QUEUE_MAX_ATTEMPTS,
                )
            except Exception:
                _log.exception("batch_queue_stuck_sweeper failed")

        scheduler.add_job(
            _batch_tick,
            trigger="interval",
            seconds=BATCH_QUEUE_TICK_SECONDS,
            id="batch_queue_tick",
            name="Batch queue worker tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            _batch_stuck_sweep,
            trigger="interval",
            seconds=BATCH_QUEUE_STUCK_SWEEPER_SECONDS,
            id="batch_queue_stuck_sweeper",
            name="Batch queue stuck-job sweeper",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _log.info("Batch queue APScheduler jobs registered")

    _graph_mode = _wire_graph_mode_handlers(config.get_graph_mode())

    # STT‑1 optional speech-to-text — ping adapter when enabled (never fatal).
    try:
        if config.get_stt_backend() != "none":
            stt = config.get_speech_to_text()
            if stt is None:
                _log.warning("STT: backend reports non-none but adapter is None — check env")
            elif stt.ping():
                _log.info("STT ping OK (%s)", type(stt).__name__)
            else:
                _log.warning("STT ping failed (%s)", type(stt).__name__)
    except RuntimeError:
        raise
    except Exception as exc:
        _log.warning("STT startup ping skipped: %s", exc)

    # Capability service registry: discover out-of-process services declared
    # in CAPABILITY_SERVICE_URLS, then immediately probe each one's health
    # endpoint so the very first GET / after startup reflects real state
    # rather than the default healthy=False placeholders. Both steps are
    # warnings-only — Core must start cleanly even when zero services are
    # reachable or when every service is down.
    capability_registry = config.get_capability_registry()
    capability_urls = config.get_capability_service_urls()
    if capability_urls:
        try:
            await capability_registry.discover(capability_urls)
            _log.info(
                "Capability registry: %d service(s) registered, %d tool(s) available",
                len(capability_registry.all_services()),
                len(capability_registry.get_tools()),
            )
        except Exception as exc:
            _log.warning("Capability registry initial discovery failed: %s", exc)

        try:
            await capability_registry.check_all_health()
            healthy = sum(1 for s in capability_registry.all_services() if s.healthy)
            _log.info(
                "Capability registry: initial health probe complete (%d/%d healthy)",
                healthy,
                len(capability_registry.all_services()),
            )
        except Exception as exc:
            _log.warning("Capability registry initial health probe failed: %s", exc)
    else:
        _log.info("Capability registry: no CAPABILITY_SERVICE_URLS configured")

    # KG Quality Pass 3: weekly edge-quality + corpus constraint maintenance job.
    # Runs Sunday 02:00 UTC (or DEDUP_CRON_HOUR_UTC, default 2).
    # Only scheduled when graph mode is `inprocess` — otherwise the job is owned
    # by the lumogis-graph service's own APScheduler (in `service` mode) or
    # not run at all (in `disabled` mode). Without this guard, both Core AND
    # the KG service would scan the same Postgres rows and double-write
    # `edge_scores` / `graph_projected_at`.
    # Also guarded with `if scheduler:` to avoid crashes in test environments.
    if scheduler and _graph_mode == "inprocess":
        from services.edge_quality import run_weekly_quality_job as _run_weekly_quality_job

        _wq_hour = config.get_dedup_cron_hour_utc()
        scheduler.add_job(
            _run_weekly_quality_job,
            trigger="cron",
            day_of_week="sun",
            hour=_wq_hour,
            minute=0,
            id="weekly_quality_maintenance",
            name="Weekly KG quality maintenance",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )
        _log.info("Weekly quality maintenance job registered (Sunday %02d:00 UTC)", _wq_hour)
    elif scheduler and _graph_mode != "inprocess":
        _log.info(
            "Weekly KG quality maintenance NOT registered on Core (graph_mode=%s — owned by lumogis-graph service)",
            _graph_mode,
        )

    # Capability registry refresh + health probe jobs run regardless of
    # graph mode — they discover and probe ALL out-of-process capability
    # services (lumogis-graph being just one of them). Both are gated on
    # `if scheduler:` only because tests may run without an APScheduler.
    if scheduler:
        # Capability registry refresh — picks up services that came online
        # after Core started, and refreshes manifests for already-registered
        # services. Job is a sync wrapper around discover_sync().
        def _refresh_capability_registry() -> None:
            capability_registry.discover_sync(config.get_capability_service_urls())

        scheduler.add_job(
            _refresh_capability_registry,
            trigger="interval",
            minutes=5,
            id="capability_registry_refresh",
            name="Capability registry refresh",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )
        _log.info("Capability registry refresh job registered (every 5 minutes)")

        # Capability service health probe — faster cadence than discovery
        # because health is cheap and operators want fresh status. Updates
        # `healthy` and `last_seen_healthy` on each RegisteredService in place.
        def _probe_capability_health() -> None:
            capability_registry.check_all_health_sync()

        scheduler.add_job(
            _probe_capability_health,
            trigger="interval",
            seconds=60,
            id="capability_health_check",
            name="Capability service health probes",
            replace_existing=True,
            misfire_grace_time=30,
            coalesce=True,
            max_instances=1,
        )
        _log.info("Capability service health probe job registered (every 60 seconds)")

    from librechat_config import generate_librechat_yaml

    if generate_librechat_yaml():
        import httpx as _httpx
        from routes.admin import _current_restart_secret

        _sc_url = os.environ.get("STACK_CONTROL_URL", "http://stack-control:9000")
        try:
            _httpx.post(
                f"{_sc_url}/restart",
                json={"services": ["librechat"]},
                headers={"X-Lumogis-Restart-Token": _current_restart_secret()},
                timeout=30,
            )
            _log.info("LibreChat restarted with generated config")
        except Exception as exc:
            _log.warning("Could not restart LibreChat: %s", exc)

    # MCP startup: build a fresh FastMCP, point the existing /mcp mount at
    # its Starlette sub-app, and enter session_manager.run() to start the
    # anyio task group that StreamableHTTPSessionManager.handle_request
    # needs.
    #
    # Why rebuild instead of reusing mcp_server.mcp?
    # StreamableHTTPSessionManager.run() can only be called once per
    # FastMCP instance. Production lifespans run once so reuse would
    # work, but TestClient(main.app) starts a fresh lifespan per test —
    # rebuilding makes both paths identical and keeps the production code
    # equally simple. The cost is ~1ms.
    if mcp_server.mcp is not None and _mcp_mount_route is not None:
        try:
            fresh = mcp_server.build_fastmcp()
            mcp_server.mcp = fresh
            _mcp_mount_route.app = fresh.streamable_http_app()
            _mcp_run_cm = fresh.session_manager.run()
            await _mcp_run_cm.__aenter__()
            app.state.mcp_run_cm = _mcp_run_cm
            _log.info("MCP session manager started")
        except Exception as exc:
            app.state.mcp_run_cm = None
            _log.warning("MCP session manager failed to start: %s", exc)
    else:
        app.state.mcp_run_cm = None

    _log.info("Startup complete")
    yield

    if getattr(app.state, "mcp_run_cm", None) is not None:
        try:
            await app.state.mcp_run_cm.__aexit__(None, None, None)
            _log.info("MCP session manager stopped")
        except Exception as exc:
            _log.warning("MCP session manager shutdown error: %s", exc)

    from services.ingest import stop_watcher

    stop_watcher()

    from signals import stop_all as stop_signal_monitors

    stop_signal_monitors()
    scheduler.shutdown(wait=False)
    _log.info("APScheduler shutdown")

    from services.webpush import shutdown_web_push_executor

    shutdown_web_push_executor()

    hooks.shutdown()
    if _graph_mode == "service":
        from services import graph_webhook_dispatcher as _gwd

        _gwd.shutdown()
    config.shutdown()
    _log.info("Shutdown complete")


app = FastAPI(title="Lumogis", description="Personal AI assistant orchestrator", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Minimal liveness — no JWT (see ``auth._AUTH_BYPASS_PREFIXES``).

    Docker healthchecks cannot send a Bearer; ``/admin/health`` requires auth
    when ``AUTH_ENABLED=true``.
    """
    return {"status": "ok"}


# Middleware ordering (plan D4a): correlation is registered FIRST so
# Starlette wraps auth as the OUTERMOST middleware. Execution flow on
# a request is therefore: auth_middleware → (sets request.state.user)
# → correlation_middleware → (binds request_id + reads request.state)
# → endpoint. Tradeoff: log lines emitted by auth_middleware itself
# before its `await call_next(request)` (e.g. early-return 401 paths)
# do NOT carry request_id — accepted scope choice for this chunk.
app.middleware("http")(correlation_middleware)
app.middleware("http")(auth_middleware)
app.include_router(auth_router)
app.include_router(admin_users_router)
app.include_router(admin_user_imports_router)
app.include_router(me_router)
app.include_router(mcp_tokens_router)
app.include_router(mcp_tokens_admin_router)
app.include_router(connector_credentials_router)
app.include_router(connector_credentials_admin_router)
app.include_router(connector_credentials_household_admin_router)
app.include_router(connector_credentials_system_admin_router)
app.include_router(admin_diagnostics_router)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(data_router)
app.include_router(signals_router)
app.include_router(scope_router)
app.include_router(actions_router)
from routes.connector_permissions import (
    router as connector_permissions_router,
    admin_router as connector_permissions_admin_router,
    admin_list_router as connector_permissions_admin_list_router,
)
app.include_router(connector_permissions_router)
app.include_router(connector_permissions_admin_router)
app.include_router(connector_permissions_admin_list_router)
app.include_router(events_router)
app.include_router(capabilities_router)

# /api/v1 web-client façade — Phase 0 surface defined by plan
# `cross_device_lumogis_web`. Mounts chat, memory, kg, approvals, audit,
# captures, notifications, and the events alias. Auth (`require_user`)
# is applied at the sub-router level.
from routes.api_v1 import router as api_v1_router  # noqa: E402

app.include_router(api_v1_router)

app.include_router(web_router)

# Mount the MCP server at /mcp when the SDK is installed. The mount
# route is created once at module-load with the initial sub-app; the
# lifespan above swaps in a freshly-built sub-app on every startup so
# that StreamableHTTPSessionManager.run() (which is single-shot per
# FastMCP instance) can be entered cleanly each time. We hold onto the
# Mount route object here so the lifespan can mutate `route.app`.
# If the mcp package is missing or the mount fails, log a warning and
# continue: Core boots normally and /capabilities still serves a manifest
# that lists the MCP surface for discovery by future clients.
_mcp_mount_route = None
if mcp_server.mcp is not None:
    try:
        app.mount("/mcp", mcp_server.mcp.streamable_http_app())
        # The just-added route is always the last one Starlette appended.
        from starlette.routing import Mount as _Mount

        for _r in reversed(app.routes):
            if isinstance(_r, _Mount) and _r.path == "/mcp":
                _mcp_mount_route = _r
                break
        _log.info("MCP server mounted at /mcp (stateless HTTP, JSON responses)")
    except Exception as exc:
        _log.warning("MCP mount failed (%s) — continuing without /mcp", exc)
else:
    _log.info("MCP server disabled (mcp package not installed)")
