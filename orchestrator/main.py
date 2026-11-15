# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""
Lumogis orchestrator – FastAPI app.

App creation, lifespan (health checks, collection init, shutdown),
and router includes. All endpoint logic lives in routes/.
"""

import importlib.util
import logging
import os
import sys
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
import mcp_server
from auth import auth_middleware
from correlation import correlation_middleware
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from plugins import load_plugins
from ports.metadata_store import PoolExhaustedError
from routes.actions import router as actions_router
from routes.admin import router as admin_router
from routes.admin_diagnostics import router as admin_diagnostics_router
from routes.admin_ollama import router as admin_ollama_router
from routes.admin_safety import router as admin_safety_router
from routes.admin_sharing import router as admin_sharing_router
from routes.admin_system_mail import router as admin_system_mail_router
from routes.admin_users import imports_router as admin_user_imports_router
from routes.admin_users import router as admin_users_router
from routes.altcha import router as altcha_router
from routes.auth import router as auth_router
from routes.capabilities import router as capabilities_router
from routes.chat import router as chat_router
from routes.connector_credentials import admin_router as connector_credentials_admin_router
from routes.connector_credentials import (
    household_admin_router as connector_credentials_household_admin_router,
)
from routes.connector_credentials import router as connector_credentials_router
from routes.connector_credentials import (
    system_admin_router as connector_credentials_system_admin_router,
)
from routes.data import router as data_router
from routes.events import register_hooks as register_sse_hooks
from routes.events import router as events_router
from routes.invites import router as invites_router
from routes.mcp_tokens import admin_router as mcp_tokens_admin_router
from routes.mcp_tokens import router as mcp_tokens_router
from routes.me import router as me_router
from routes.me_sessions import router as me_sessions_router
from routes.password_reset import router as password_reset_router
from routes.scope import router as scope_router
from routes.signals import router as signals_router
from routes.web import router as web_router

import config

_log = logging.getLogger(__name__)

_FALSEY_ENV = ("false", "0", "no")

# LUM-762: the legacy spelling is `ACTION_PROPOSALS_QUEUE_ENABLED`, which gated a
# SWEEPER and never gated a queue — part of why this module read as a live queue for
# three releases. Remove the fallback next release.
_PROPOSAL_SWEEPER_ENV = "ACTION_PROPOSALS_SWEEPER_ENABLED"
_PROPOSAL_SWEEPER_ENV_LEGACY = "ACTION_PROPOSALS_QUEUE_ENABLED"


def _proposal_sweeper_enabled(env) -> bool:
    """Resolve the stale-claim sweeper flag, honouring the legacy spelling.

    Extracted as a pure function so the env precedence is testable. Asserting it
    through APScheduler job registration would need a lifespan harness that does
    not exist for ANY queue in this tree, which is disproportionate to an env
    rename — and the precedence is the part that can actually be wrong.
    """
    raw = env.get(_PROPOSAL_SWEEPER_ENV)
    if raw is None:
        raw = env.get(_PROPOSAL_SWEEPER_ENV_LEGACY)
        if raw is not None:
            _log.warning(
                "%s is deprecated and will be removed next release; use %s "
                "(it gates the stale-claim sweeper, not a queue)",
                _PROPOSAL_SWEEPER_ENV_LEGACY,
                _PROPOSAL_SWEEPER_ENV,
            )
    if raw is None:
        return True
    return raw.lower() not in _FALSEY_ENV


# Qdrant collections created on startup.
# "signals" stores embedded content summaries for semantic dedup.
# Vector size 768 matches Nomic Embed. Changing later requires drop + re-index.
_COLLECTIONS = ["documents", "conversations", "entities", "signals", "memories"]


def _wire_graph_mode_handlers(graph_mode: str) -> str:
    """Wire Core to the graph layer per validated ``GRAPH_MODE``.

    Returns the **effective** mode Core runs after fail-soft degradation.
    ``service`` / ``inprocess`` may become ``disabled`` when the optional graph
    component's wiring or the in-process plugin package is absent (one WARNING per degraded path).

    Callers normally pass validated tokens from :func:`config.read_graph_mode_from_env`.
    A non-canonical literal defends depth-first by returning ``disabled`` with a WARNING.

    Extracted so tests can exercise branching without the full lifespan.
    """
    if graph_mode not in ("service", "inprocess", "disabled"):
        _log.warning(
            "Ignoring invalid graph wiring token — falling back to disabled",
            extra={
                "event": "graph_mode_fallback",
                "requested_mode": graph_mode,
                "effective_mode": "disabled",
                "reason": "invalid_graph_mode_env",
            },
        )
        return "disabled"

    effective_mode = graph_mode
    if graph_mode == "service":
        try:
            from services.graph_webhook_dispatcher import register_core_callbacks
            from services.kg_premium_core import register_query_graph_proxy

            register_core_callbacks()
            register_query_graph_proxy()
            _log.info("Graph mode: service — KG webhooks + /context proxy enabled")
        except ImportError as exc:
            effective_mode = "disabled"
            _log.warning(
                "Graph service wiring unavailable — falling back to disabled",
                extra={
                    "event": "graph_mode_fallback",
                    "requested_mode": graph_mode,
                    "effective_mode": "disabled",
                    "reason": "service_import_error",
                    "exc_type": type(exc).__name__,
                },
            )
    elif graph_mode == "disabled":
        _log.info("Graph mode: disabled — no graph functionality")
    else:
        if importlib.util.find_spec("plugins.graph") is None:
            effective_mode = "disabled"
            _log.warning(
                "GRAPH_MODE=inprocess but plugins.graph package is absent "
                "— falling back to disabled",
                extra={
                    "event": "graph_mode_fallback",
                    "requested_mode": graph_mode,
                    "effective_mode": "disabled",
                    "reason": "inprocess_plugin_absent",
                },
            )
        else:
            _log.info("Graph mode: inprocess — graph plugin runs inside Core")
    return effective_mode


def _bind_host_is_loopback(host: str) -> bool:
    """Classify ``LUMOGIS_BIND_HOST`` for the exposure guard (LUM-473 Chunk A).

    Returns ``True`` only for the loopback interface (and the literal
    ``localhost``); **everything else** — private LAN IPs, link-local, public,
    or any unparseable/hostname value — is treated as **non-loopback** so the
    guard fails safe. This is deliberately stricter than
    ``config._stt_sidecar_host_allowed`` (which also permits private/link-local
    and single-label hostnames): a Core bound to a private LAN address is
    *exactly* the exposed case that must mandate auth.

    An empty/unset value maps to loopback because the native launcher defaults
    ``--host`` to ``127.0.0.1`` and only sets ``LUMOGIS_BIND_HOST`` explicitly.
    The Docker path never sets it, so the guard stays passive there even though
    the container binds ``0.0.0.0`` (host port-mapping, not the container bind,
    decides real exposure) — see the plan's Docker no-regression note.
    """
    import ipaddress

    h = host.strip()
    if not h or h.lower() == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        # Hostname / unparseable → cannot prove loopback → fail safe (exposed).
        return False
    embedded = getattr(addr, "ipv4_mapped", None)
    if embedded is not None:
        addr = embedded
    return addr.is_loopback


def _run_post_bootstrap_default_user_remap() -> None:
    """Second-chance remap after ``bootstrap_if_empty`` on first auth-on boot.

    The docker-entrypoint / native launcher invoke ``db_default_user_remap.py``
    **before** uvicorn starts. On the single→multi flip (LUM-473 household
    sharing enable), that pass always races the empty ``users`` table: the
    bootstrap admin email is set but the row does not exist yet, so legacy
    ``user_id='default'`` rows are not remapped and the new admin's library
    appears empty until an unrelated restart.

    This pass is idempotent — when the pre-uvicorn remap already succeeded it
    is a no-op.
    """
    try:
        import db_default_user_remap

        rc = db_default_user_remap.main(post_bootstrap=True)
        if rc != 0:
            _log.warning(
                "post-bootstrap default-user remap exited %d — legacy "
                "'default' rows may remain until the next restart",
                rc,
            )
    except Exception:
        _log.exception("post-bootstrap default-user remap failed")


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

    With ``AUTH_ENABLED=true`` it also refuses a missing or placeholder
    ``AUTH_SECRET`` or ``LUMOGIS_CREDENTIAL_KEY[S]``, and (LUM-885) a missing
    or placeholder refresh secret, or one equal to ``AUTH_SECRET``.
    """
    import hmac

    import services.users as users_svc
    from auth import auth_enabled
    from services._credential_internals import _PLACEHOLDER_KEYS

    # Exposure invariant (LUM-473 Chunk A): auth must NEVER be off on a
    # non-loopback bind. Checked BEFORE the test escape hatch below because it
    # is an independent security guard (not about the users table) and no
    # existing test sets LUMOGIS_BIND_HOST — so it stays passive (loopback
    # default) everywhere except a deliberately exposed Core. Dormant in Chunk A
    # (Core binds 127.0.0.1); live for Chunk B when a proxy fronts the LAN.
    bind_host = os.environ.get("LUMOGIS_BIND_HOST", "127.0.0.1")
    if not _bind_host_is_loopback(bind_host) and not auth_enabled():
        raise RuntimeError(
            "LUMOGIS_BIND_HOST=%r is a non-loopback interface but "
            "AUTH_ENABLED=false — refusing to boot. Exposing Core beyond "
            "loopback without authentication would let anyone on the network "
            "read every user's data. Either bind to 127.0.0.1 (loopback) or "
            "set AUTH_ENABLED=true with a bootstrap admin, AUTH_SECRET, and "
            "LUMOGIS_CREDENTIAL_KEY. See LUM-473." % bind_host
        )

    # Test-only escape hatch — NEVER set in production, NEVER read from
    # any other module. Allows tests that exercise AUTH_ENABLED=true
    # (e.g. asserting a route returns 401) to boot without seeding a
    # real users-table row through the mocked metadata store. The
    # leading underscore + `_TEST_` segment + `_DO_NOT_SET_IN_PRODUCTION`
    # suffix flag the variable as private to the test harness; it lives
    # only in `orchestrator/tests/conftest.py` (autouse fixture).
    _test_skip = (
        os.environ.get("_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION", "")
        .strip()
        .lower()
    )
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
        if secret in _PLACEHOLDER_KEYS:
            raise RuntimeError(
                "AUTH_ENABLED=true but AUTH_SECRET is unset or a placeholder — "
                "refusing to boot. Generate a real secret with "
                '`openssl rand -hex 32` (or `python3 -c "import secrets; '
                'print(secrets.token_hex(32))"`) and set it as AUTH_SECRET in '
                ".env, then restart. The entrypoint auto-rotates JWT_SECRET / "
                "JWT_REFRESH_SECRET / RESTART_SECRET but intentionally does NOT "
                "auto-rotate AUTH_SECRET — operators flip family-LAN mode on "
                "deliberately and must own the access-token signing secret."
            )

        # Refresh-secret hardening (LUM-885, security audit #2 AUTH-002): the
        # refresh secret must be a real value and must differ from AUTH_SECRET.
        # Equal secrets let a refresh JWT verify as an access token on any
        # verifier that does not check ``typ``. The check sits here (inside
        # ``if enabled:``, after the AUTH_SECRET block) so it follows the test
        # escape hatch exactly like AUTH_SECRET. The messages name the variable
        # the value came from and never contain either secret value.
        from auth import _refresh_secret

        refresh = _refresh_secret()
        if refresh in _PLACEHOLDER_KEYS:
            if os.environ.get("LUMOGIS_JWT_REFRESH_SECRET", "").strip():
                source = "LUMOGIS_JWT_REFRESH_SECRET is a placeholder"
            elif os.environ.get("JWT_REFRESH_SECRET", "").strip():
                source = "JWT_REFRESH_SECRET is a placeholder"
            else:
                source = "neither LUMOGIS_JWT_REFRESH_SECRET nor JWT_REFRESH_SECRET is set"
            raise RuntimeError(
                "AUTH_ENABLED=true but the refresh secret is unusable (%s) — "
                "refusing to boot. Set a real refresh secret that differs from "
                "AUTH_SECRET, for example `openssl rand -hex 32`, as "
                "JWT_REFRESH_SECRET (or LUMOGIS_JWT_REFRESH_SECRET) in .env, then "
                "restart. The compose entrypoint fills JWT_REFRESH_SECRET "
                "automatically when it is missing, empty or the placeholder, but "
                "never overrides a non-empty LUMOGIS_JWT_REFRESH_SECRET." % source
            )
        if hmac.compare_digest(refresh.encode("utf-8"), secret.encode("utf-8")):
            raise RuntimeError(
                "AUTH_ENABLED=true but the refresh secret equals AUTH_SECRET — "
                "refusing to boot. A refresh token must not verify as an access "
                "token. Set a distinct JWT_REFRESH_SECRET (or "
                "LUMOGIS_JWT_REFRESH_SECRET), for example `openssl rand -hex 32`, "
                "and restart. Existing sessions sign in again once."
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
        if cred_keys in _PLACEHOLDER_KEYS:
            raise RuntimeError(
                "AUTH_ENABLED=true but LUMOGIS_CREDENTIAL_KEY[S] is unset or a "
                "placeholder — refusing to boot. Generate a real key with "
                '`python3 -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"` and set it as '
                "LUMOGIS_CREDENTIAL_KEY in .env (or LUMOGIS_CREDENTIAL_KEYS "
                "for a CSV during rotation), then restart. The entrypoint "
                "intentionally does NOT auto-rotate this key — losing it "
                "makes every per-user connector credential unrecoverable."
            )


def _requested_worker_count(argv=None, environ=None):
    """Return ``(count, source)`` for an explicitly requested worker count.

    ``None`` when nothing asked for one. Mirrors uvicorn's own precedence
    (``uvicorn/config.py``): an explicit ``--workers`` wins, and
    ``WEB_CONCURRENCY`` is consulted only when the flag is absent. A value
    that is not an integer is ignored here — uvicorn raises on it first, and
    guessing at operator intent from a malformed number is worse than letting
    the launcher's own error speak.

    ``sys.argv`` is readable from a spawned uvicorn worker too
    (``multiprocessing.spawn`` carries it into the child), so this sees the
    flag whichever process runs it.
    """
    argv = list(sys.argv if argv is None else argv)
    environ = os.environ if environ is None else environ

    for i, arg in enumerate(argv):
        raw = None
        if arg.startswith("--workers="):
            raw = arg.split("=", 1)[1]
        elif arg == "--workers" and i + 1 < len(argv):
            raw = argv[i + 1]
        if raw is not None:
            try:
                return int(raw.strip()), "--workers"
            except ValueError:
                return None

    env_raw = environ.get("WEB_CONCURRENCY", "").strip()
    if env_raw:
        try:
            return int(env_raw), "WEB_CONCURRENCY"
        except ValueError:
            return None
    return None


def _enforce_single_worker(argv=None, environ=None):
    """Refuse to boot when more than one worker process was asked for (LUM-832).

    Lumogis holds five pieces of correctness-bearing state **in process**:
    rate-limit buckets, the ``tv`` token-version cache, the ``sid`` revocation
    LRU (LUM-29 / LUM-243), the batch queue (LUM-407) and the
    connector-permission cache (LUM-34) — plus a Postgres pool sized per
    process (LUM-816). A second worker gives each its own copy, so a 30/min
    limit becomes 30xN and a revoked session stays valid on the workers that
    did not serve the revocation.

    None of that crashes. It is silent incorrectness, which is why this is a
    refusal and not the warning it replaced: an operator who sets
    ``WEB_CONCURRENCY=4`` to make things faster gets a stack that appears to
    work and has quietly stopped enforcing rate limits and session revocation.
    A log line at boot does not reach them. Same posture as migration ``074``
    — a partial schema is worse than a refusal, and the refusal names the
    remedy rather than emitting a bare error.

    Making any of the five multi-worker-safe is explicitly out of scope; this
    says the mode is unsupported, it does not schedule supporting it.

    Raising here refuses cleanly rather than crash-looping: uvicorn's
    ``Multiprocess.keep_subprocess_alive`` special-cases ``STARTUP_FAILURE``
    and stops the *parent* instead of respawning the worker (verified against
    uvicorn 0.52), so the operator gets one refusal and an exited stack.
    """
    requested = _requested_worker_count(argv=argv, environ=environ)
    if requested is None:
        return
    count, source = requested
    if count <= 1:
        return
    remedy = (
        "drop the `--workers` flag from the uvicorn command (or pass `--workers 1`)"
        if source == "--workers"
        else "unset WEB_CONCURRENCY in .env (or set it to 1)"
    )
    raise RuntimeError(
        f"STARTUP FAILED: {source}={count} — Lumogis does not support more than "
        "one orchestrator worker process, and refuses to boot rather than run "
        "incorrectly. Rate limiting, session revocation, the token-version "
        "cache, the batch queue and the connector-permission cache are all "
        "held IN PROCESS: with N workers a 30/min rate limit becomes 30xN, and "
        "a revoked session keeps working on every worker that did not serve "
        "the revocation. The Postgres pool is per process too, so N workers "
        "open N x LUMOGIS_PG_POOL_MAX connections. None of this fails loudly, "
        f"which is why it is refused here. Remedy: {remedy}, then restart — "
        "one worker is the shipped and only supported mode. To serve more "
        "load, run a bigger box or put a cache in front; do not add workers. "
        "See LUM-832, LUM-816, LUM-407."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # LUM-832 — refuse a multi-worker boot before anything opens a connection.
    # First in the lifespan on purpose: there is no point sizing a pool or
    # pinging backends for a process that must not come up.
    _enforce_single_worker()

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

    # LUM-571: process-wide egress ceiling (opt-in, default off). After store
    # health checks so the allowlist is built from a live config and the rest of
    # startup runs under the ceiling. Fails closed (refuses boot) if enabled but
    # `tethered` is unavailable.
    from services.egress_guard import activate_ceiling

    activate_ceiling()

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
    _run_post_bootstrap_default_user_remap()
    _enforce_auth_consistency()
    # LUM-918: ACCESS_TOKEN_TTL_SECONDS is clamped to an hour; say so at boot
    # when the operator asked for more.
    from auth import warn_if_access_token_ttl_clamped

    warn_if_access_token_ttl_clamped()
    # Plan llm_provider_keys_per_user_migration Pass 2.6, generalised to every
    # background model task by LUM-877: refuse to boot if a task that runs
    # without a user has no keyless model candidate under AUTH_ENABLED=true (no
    # per-user key resolution is possible for non-user-scoped sources). Also
    # warns about retired env vars and a bad ``tasks:`` block. The shipped
    # defaults + auth-off both pass silently.
    config._check_background_model_defaults()

    # Embedder is a soft requirement — start in degraded mode if model unavailable.
    # Bundled Hub may start Core before Ollama is warm; a retry job flips ready later.
    from services.embedding_readiness import try_activate_embedding

    app.state.embedding_ready = False
    if not try_activate_embedding(app.state):
        _log.warning(
            "Embedding model not ready at startup (Ollama unreachable or %s not pulled). "
            "Search and ingest will be unavailable until the model is ready. "
            "Open http://localhost:8000/dashboard → Settings → Models to pull the model, "
            "or check that the Ollama service is running.",
            os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
        )
    _embedding_ready = app.state.embedding_ready

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
            _log.warning(
                "Reranker warmup failed (%s) — search will proceed without reranking",
                exc,
            )
            config._instances["reranker"] = None
    elif reranker is not None:
        _log.info("Reranker warmup skipped — embedding not ready")
    else:
        _log.info("Reranker disabled (RERANKER_BACKEND=none)")

    from permissions import seed_defaults

    seed_defaults()

    try:
        config.warmup_injection_sanitiser()
    except RuntimeError:
        _log.exception("Injection sanitiser warmup failed — refusing startup")
        raise

    plugin_routers = load_plugins()
    for plugin_router in plugin_routers:
        app.include_router(plugin_router)

    import platform

    from services.ingest import schedule_inbox_poll
    from services.ingest import start_watcher

    _log.info(
        "Inbox pipeline platform: %s",
        " ".join(platform.uname()),
    )
    # LUM-922: the admin's stop-entities list lives in the workspace now. Copy the
    # old in-image list there once; an existing file is never overwritten.
    config.ensure_stop_entities_file()

    inbox_mode = config.get_inbox_mode()
    inbox_owner = config.get_inbox_owner_user_id()
    if inbox_mode != "off" and inbox_owner:
        config.ensure_inbox_directory()
    if inbox_mode == "event":
        start_watcher()

    from services.ingest import enqueue_initial_ingest_scan
    from services.ingest import start_ingest_path_watchers

    start_ingest_path_watchers()
    # Hub wizard sets LUMOGIS_DEFER_LIBRARY_INDEX — bulk scan is triggered only
    # from POST /bundled/start-library-index after models are ready. Otherwise
    # defer until the embedder is live (warm Docker) or the readiness retry.
    if _library_index_deferred():
        from services.index_bootstrap import prior_library_index_exists

        if _library_resync_on_start() and _embedding_ready and prior_library_index_exists():
            enqueue_initial_ingest_scan()
            _log.info("Library index resync enqueued on Hub cold start (prior index present)")
        else:
            _log.info("Library index scan deferred — Hub wizard will trigger explicitly")
    elif _embedding_ready:
        enqueue_initial_ingest_scan()
    else:
        _log.info("Initial library scan deferred until the embedding model is ready")

    # APScheduler: start scheduler then load signal sources.
    scheduler = config.get_scheduler()
    scheduler.start()
    _log.info("APScheduler started")

    if inbox_mode == "poll":
        schedule_inbox_poll()

    from signals import start_all as start_signal_monitors

    start_signal_monitors()

    from services.egress_deny_tail import start as start_egress_deny_tail

    start_egress_deny_tail()

    # Register SSE hook callbacks and notification dispatcher.
    from services.notifications.migrate_webpush_prefs import reset_notify_on_signals_for_consent
    from services.notifications.migrate_webpush_prefs import run_if_needed

    run_if_needed()
    # LUM-648: one-time fresh-consent reset now that notify_on_signals is a live gate.
    reset_notify_on_signals_for_consent()

    from services.notifications.dispatcher import register_hook_listeners

    register_hook_listeners()

    # LUM-645: opt-in mailbox-organization go-forward hook.
    from services import email_organize

    email_organize.register_hooks()

    register_sse_hooks()

    from services.routines import register_all as register_routines

    register_routines()

    # LUM-625 Layer 2: when IMAP egress containment is enabled, keep the Squid
    # email-IMAP allow file in sync with configured mailboxes (regen on credential
    # change). Since LUM-758 the proxy APPLIES the file itself — its entrypoint
    # watches the allow directory — so this no longer waits on an operator.
    # Best-effort — never blocks startup.
    #
    # Note this is gated on the Layer-1 flag alone, which says nothing about
    # whether a proxy is deployed; email_egress_acl._should_notify carries the
    # extra condition so a flag-on/no-proxy install never notifies about an
    # egress surface nothing is enforcing.
    from services.egress_guard import imap_egress_enabled

    if imap_egress_enabled():
        try:
            from services.email_egress_acl import register as register_imap_egress_acl

            register_imap_egress_acl()
        except Exception:  # noqa: BLE001 - audit-layer wiring must not break startup
            _log.exception("email-IMAP egress allow-file listener registration failed")

    # LUM-980: warn when an install from the release files runs an image newer than
    # its docker-compose.yml. Read-only and advisory.
    try:
        from services.compose_freshness import startup_result as compose_freshness

        compose_freshness()
    except Exception:  # noqa: BLE001 - advisory check must not break startup
        _log.exception("compose freshness check failed")

    # LUM-728 — notice the reverse ordering: paperless connected AFTER a mailbox
    # already has filing rules, which the rule-creation gate can never catch.
    try:
        from services.paperless_overlap import register as register_paperless_overlap

        register_paperless_overlap()
    except Exception:  # noqa: BLE001 - advisory wiring must not break startup
        _log.exception("paperless overlap listener registration failed")

    # LUM-794: act-tier action handlers register UNCONDITIONALLY. Module import runs
    # the module-level register_action() calls (LUM-623) — there is no scheduler or
    # queue dependency in them, and no auto-discovery.
    #
    # These two imports used to sit inside `if scheduler and _batch_queue_enabled:`
    # below, so two unrelated features shared one flag: `BATCH_QUEUE_ENABLED=false`,
    # or any scheduler-less startup, left the action registry EMPTY and every
    # legitimately approved proposal failed with `Unknown action`. Harmless-looking
    # until LUM-762 made failure terminal — those rows used to return to `approved`
    # and survive the misconfiguration; now they die on the first attempt, and until
    # LUM-793 ships a `dead` row appears on no surface at all. A configuration
    # mistake should not silently destroy a household member's approved email.
    from actions.handlers import imap_writeback as _imap_writeback_registered  # noqa: F401
    from actions.handlers import smtp_send as _smtp_send_registered  # noqa: F401

    _batch_queue_enabled = os.environ.get("BATCH_QUEUE_ENABLED", "true").lower() not in (
        "false",
        "0",
        "no",
    )
    if scheduler and _batch_queue_enabled:
        from uuid import uuid4

        from services.batch_queue import BATCH_QUEUE_MAX_ATTEMPTS
        from services.batch_queue import BATCH_QUEUE_STUCK_AFTER_SECONDS
        from services.batch_queue import BATCH_QUEUE_STUCK_SWEEPER_SECONDS
        from services.batch_queue import BATCH_QUEUE_TICK_DRAIN_LIMIT
        from services.batch_queue import BATCH_QUEUE_TICK_SECONDS
        from services.batch_queue import _run_one_tick
        from services.batch_queue import reset_stuck

        from services import batch_handlers as _batch_handlers_registered  # noqa: F401

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

        # Startup crash-recovery. Any job still marked 'running' at boot was orphaned by the
        # previous container: its worker process is gone (e.g. POST /settings/restart
        # force-recreates this orchestrator mid-ingest). Reclaim immediately (stuck_after=0)
        # instead of waiting up to BATCH_QUEUE_STUCK_AFTER_SECONDS (default 1800s) for the
        # periodic sweeper — otherwise the per-user concurrency cap leaves the queue
        # head-of-line blocked and post-restart folder re-ingestion stalls for ~30 minutes.
        try:
            _reclaimed = reset_stuck(
                # safe: Lumogis runs a single orchestrator instance — no concurrent
                # workers that could hold a legitimate 'running' lock across this boot.
                stuck_after_seconds=0,
                max_attempts=BATCH_QUEUE_MAX_ATTEMPTS,
            )
            if _reclaimed:
                _log.info(
                    "batch_queue: reclaimed %d orphaned 'running' job(s) at startup",
                    _reclaimed,
                )
        except Exception:
            _log.exception("batch_queue startup reclaim failed")

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

    # LUM-65: drain the system-mail outbox. This job is why the public
    # password-reset request can answer in constant time — the SMTP submission
    # happens here, off the request path, so neither the enumeration timing
    # guarantee nor the shared AnyIO threadpool depends on how healthy the
    # operator's relay is today.
    def _system_mail_drain() -> None:
        from services import system_mail as _system_mail

        try:
            _system_mail.drain_outbox()
        except Exception:
            _log.exception("system_mail: outbox drain tick failed")

    def _system_mail_purge() -> None:
        from services import system_mail as _system_mail

        try:
            _system_mail.purge_expired()
        except Exception:
            _log.exception("system_mail: outbox purge failed")

    # `if scheduler:` for the same reason as the blocks above — tests run
    # without an APScheduler. Without the guard the `except` below would
    # swallow an AttributeError on None and leave the drain silently
    # unregistered behind a traceback that looks like a real failure.
    if scheduler:
        from services import system_mail as _system_mail_cfg

        scheduler.add_job(
            _system_mail_drain,
            trigger="interval",
            seconds=_system_mail_cfg.drain_interval_seconds(),
            id="system_mail_outbox_drain",
            name="System mail outbox drain",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            _system_mail_purge,
            trigger="interval",
            seconds=3600,
            id="system_mail_outbox_purge",
            name="System mail outbox expiry purge",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _log.info("System mail outbox APScheduler jobs registered")

    _proposals_q_en = _proposal_sweeper_enabled(os.environ)
    if scheduler and _proposals_q_en:
        from services.proposal_queue import ACTION_PROPOSALS_CLAIM_STUCK_AFTER_SECONDS
        from services.proposal_queue import ACTION_PROPOSALS_CLAIM_SWEEPER_SECONDS
        from services.proposal_queue import reset_stuck_claims

        def _proposal_stuck_sweep() -> None:
            try:
                reset_stuck_claims(
                    stuck_after_seconds=ACTION_PROPOSALS_CLAIM_STUCK_AFTER_SECONDS,
                )
            except Exception:
                _log.exception("proposal_queue_stuck_sweeper failed")

        scheduler.add_job(
            _proposal_stuck_sweep,
            trigger="interval",
            seconds=ACTION_PROPOSALS_CLAIM_SWEEPER_SECONDS,
            id="proposal_queue_stuck_sweeper",
            name="Action proposals stuck-claim sweeper",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _log.info("Action proposals queue APScheduler stuck sweeper registered")

    if scheduler:
        from services.memory_purge import PURGE_SWEEPER_INTERVAL_SECONDS
        from services.memory_purge import sweep_partial_purges

        def _purge_partial_sweep() -> None:
            try:
                sweep_partial_purges()
            except Exception:
                _log.exception("purge_partial_sweep failed")

        scheduler.add_job(
            _purge_partial_sweep,
            trigger="interval",
            seconds=PURGE_SWEEPER_INTERVAL_SECONDS,
            id="purge_partial_sweep",
            name="Partial conversation purge reconciliation sweeper",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _log.info(
            "Purge reconciliation sweeper registered (interval=%ds)", PURGE_SWEEPER_INTERVAL_SECONDS
        )

    if scheduler:
        # LUM-695: reclaim staged email attachments. Keyed on PROPOSAL STATE,
        # not wall clock — a live proposal's attachments are never swept however
        # old, because proposals themselves never expire. Idempotent and bounded
        # per tick, so a missed run costs nothing but a later reclaim.
        from services.email_attachment_staging import SWEEP_INTERVAL_SECONDS
        from services.email_attachment_staging import sweep as sweep_staged_attachments

        def _email_attachment_staging_sweep() -> None:
            try:
                sweep_staged_attachments()
            except Exception:
                _log.exception("email_attachment_staging_sweep failed")

        scheduler.add_job(
            _email_attachment_staging_sweep,
            trigger="interval",
            seconds=SWEEP_INTERVAL_SECONDS,
            id="email_attachment_staging_sweep",
            name="Email attachment staging sweeper",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _log.info(
            "Email attachment staging sweeper registered (interval=%ds)",
            SWEEP_INTERVAL_SECONDS,
        )

        # LUM-702 — auto-file opted-in mailboxes' attachments into the document
        # library. Off for every mailbox until someone opts in, so registering the
        # job costs one no-op query per tick on an installation that never uses it.
        from services.email_attachment_ingest import INGEST_TICK_INTERVAL_SECONDS
        from services.email_attachment_ingest import ingest_tick as _attachment_ingest_tick

        def _email_attachment_ingest_sweep() -> None:
            # One unhandled exception at the top of a tick would kill the job for
            # the process's lifetime, so the tick body is wrapped like every
            # sibling job in this file.
            try:
                _attachment_ingest_tick()
            except Exception:
                _log.exception("email_attachment_ingest_sweep failed")

        scheduler.add_job(
            _email_attachment_ingest_sweep,
            trigger="interval",
            seconds=INGEST_TICK_INTERVAL_SECONDS,
            id="email_attachment_ingest_sweep",
            name="Email attachment document ingest",
            replace_existing=True,
            # Without max_instances=1 a slow mailbox lets tick N+1 start while N
            # is still fetching, and two sweeps racing uq_..._part turn a bounded
            # job into duplicate-key errors.
            max_instances=1,
            coalesce=True,
        )
        _log.info(
            "Email attachment document ingest registered (interval=%ds)",
            INGEST_TICK_INTERVAL_SECONDS,
        )

    _graph_mode = _wire_graph_mode_handlers(config.read_graph_mode_from_env())
    config.set_effective_graph_mode_for_process(_graph_mode)

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
        try:
            from services.edge_quality import run_weekly_quality_job as _run_weekly_quality_job
        except ImportError as exc:
            _log.warning(
                "Weekly KG quality maintenance not scheduled — edge_quality unavailable",
                extra={
                    "event": "weekly_quality_job_unavailable",
                    "reason": "edge_quality_import_error",
                    "exc_type": type(exc).__name__,
                },
            )
        else:
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
            "Weekly KG quality maintenance NOT registered on Core "
            "(graph_mode=%s — owned by lumogis-graph service)",
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

        def _embedding_readiness_retry() -> None:
            if try_activate_embedding(app.state):
                try:
                    from services.ingest import enqueue_initial_ingest_scan

                    if not _library_index_deferred():
                        enqueue_initial_ingest_scan()
                    else:
                        from services.index_bootstrap import prior_library_index_exists

                        if _library_resync_on_start() and prior_library_index_exists():
                            enqueue_initial_ingest_scan()
                            _log.info(
                                "Library index resync enqueued after embedding ready "
                                "(Hub cold start)"
                            )
                except Exception:
                    _log.exception("Failed to enqueue initial library scan after embedding ready")
                try:
                    scheduler.remove_job("embedding_readiness_retry")
                    _log.info("Embedding readiness retry job removed — embedder active")
                except Exception:
                    pass

        if not getattr(app.state, "embedding_ready", False):
            scheduler.add_job(
                _embedding_readiness_retry,
                trigger="interval",
                seconds=15,
                id="embedding_readiness_retry",
                name="Embedding readiness retry",
                replace_existing=True,
                misfire_grace_time=30,
                coalesce=True,
                max_instances=1,
            )
            _log.info("Embedding readiness retry job registered (every 15 seconds)")

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

    from services.ingest import stop_ingest_path_watchers
    from services.ingest import stop_watcher
    from services.ingest import unschedule_inbox_poll

    stop_ingest_path_watchers()
    stop_watcher()
    unschedule_inbox_poll()

    from signals import stop_all as stop_signal_monitors

    stop_signal_monitors()

    from services.egress_deny_tail import stop as stop_egress_deny_tail

    stop_egress_deny_tail()
    scheduler.shutdown(wait=False)
    _log.info("APScheduler shutdown")

    from services.webpush import shutdown_web_push_executor

    shutdown_web_push_executor()

    hooks.shutdown()
    if _graph_mode == "service":
        try:
            from services import graph_webhook_dispatcher as _gwd

            _gwd.shutdown()
        except ImportError:
            pass
    config.shutdown()
    _log.info("Shutdown complete")


app = FastAPI(title="Lumogis", description="Personal AI assistant orchestrator", lifespan=lifespan)


@app.exception_handler(PoolExhaustedError)
def _pool_exhausted(request: Request, exc: PoolExhaustedError) -> JSONResponse:
    """Every Postgres connection is busy — that is 503, not 500 (LUM-816).

    A 500 says the server is broken. This one says it is *busy*: the database is
    healthy, the pool is simply full, and the same request will succeed shortly.
    The distinction is what a client needs to decide whether retrying is
    sensible, and it is what stops an operator hunting a fault that is really a
    sizing problem — the message names ``LUMOGIS_PG_POOL_MAX``.

    ``Retry-After: 1`` because the caller already waited out the checkout
    deadline before this was raised.
    """
    _log.warning("pool exhausted serving %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database connections are all busy; please retry."},
        headers={"Retry-After": "1"},
    )


def _library_index_deferred() -> bool:
    """When set (Lumogis Hub bundled wizard), skip auto bulk library scan."""
    return os.environ.get("LUMOGIS_DEFER_LIBRARY_INDEX", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _library_resync_on_start() -> bool:
    """Hub post-onboarding cold start — resync files added while the app was quit."""
    return os.environ.get("LUMOGIS_LIBRARY_RESYNC_ON_START", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


@app.post("/bundled/start-library-index")
def bundled_start_library_index(request: Request) -> dict[str, str]:
    """Hub wizard step — enqueue the first bulk ``ingest_folder`` scan.

    Loopback-only bundled mode: requires ``LUMOGIS_DEFER_LIBRARY_INDEX`` and a
    live embedder. Idempotent once the scan has been queued.
    """
    if not _library_index_deferred():
        raise HTTPException(status_code=404, detail="not_available")
    if not getattr(request.app.state, "embedding_ready", False):
        raise HTTPException(status_code=503, detail="embedding_not_ready")
    from services.ingest import enqueue_initial_ingest_scan

    if not enqueue_initial_ingest_scan():
        raise HTTPException(status_code=503, detail="library_scan_unavailable")
    return {"status": "queued"}


@app.get("/healthz")
def healthz(request: Request) -> dict[str, str]:
    """Minimal liveness — no JWT (see ``auth._AUTH_BYPASS_PREFIXES``).

    Docker healthchecks cannot send a Bearer; ``/admin/health`` requires auth
    when ``AUTH_ENABLED=true``.
    """
    from services.index_bootstrap import index_bootstrap_status
    from services.ingest import watcher_status

    payload: dict[str, str] = {"status": "ok"}
    payload.update(watcher_status())
    payload.update(index_bootstrap_status())
    payload["embedding_ready"] = (
        "true" if getattr(request.app.state, "embedding_ready", False) else "false"
    )
    return payload


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
app.include_router(invites_router)
app.include_router(password_reset_router)
app.include_router(altcha_router)
app.include_router(admin_system_mail_router)
app.include_router(admin_users_router)
app.include_router(admin_user_imports_router)
app.include_router(me_router)
app.include_router(me_sessions_router)
app.include_router(mcp_tokens_router)
app.include_router(mcp_tokens_admin_router)
app.include_router(connector_credentials_router)
app.include_router(connector_credentials_admin_router)
app.include_router(connector_credentials_household_admin_router)
app.include_router(connector_credentials_system_admin_router)
app.include_router(admin_diagnostics_router)
app.include_router(admin_ollama_router)
app.include_router(admin_safety_router)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(data_router)
app.include_router(signals_router)
app.include_router(scope_router)
app.include_router(admin_sharing_router)
app.include_router(actions_router)
from routes.connector_permissions import (
    admin_list_router as connector_permissions_admin_list_router,
)
from routes.connector_permissions import admin_router as connector_permissions_admin_router
from routes.connector_permissions import router as connector_permissions_router

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
