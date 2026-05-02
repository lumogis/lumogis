# ADR 019: Structured audit and operational logging
**Status:** Finalised
**Created:** 2026-04-20
**Last updated:** 2026-04-21
**Decided by:** /explore (Claude Opus 4.7)
**Finalised by:** /verify-plan 2026-04-21 (composer-2, Claude Opus 4.7)
**Plan:** *(maintainer-local only; not part of the tracked repository)*
**Exploration:** *(maintainer-local only; not part of the tracked repository)*
**Draft mirror:** *(maintainer-local only; not part of the tracked repository)*

## Context

Lumogis separates **permission checks** (`action_log` via `permissions.log_action`) from **action execution** (`audit_log` via `actions/audit.write_audit`), both append-only in Postgres and documented for security review. Application **operational** logging remains unstructured text via stdlib `basicConfig` in `orchestrator/main.py`, which limits machine parsing, correlation across requests/MCP, and consistent field shapes for operators. The project needs a local-first, low-footprint approach that does not replace or weaken the existing domain audit tables.

## Decision

Adopt **structlog** with **stdlib integration**, configured once at orchestrator startup, emitting **JSON lines to stdout in production** (operator-controlled via env) and human-readable console output in development. Treat **Postgres `audit_log` / `action_log` as the system of record for domain audit**; optionally evolve summary columns toward **JSONB-typed payloads** in a follow-up schema chunk when queryability warrants it. **Do not** add a default OpenTelemetry Collector or generic PostgreSQL DML trigger auditing as part of this decision. OpenTelemetry export remains an opt-in future extension if distributed tracing becomes a first-class requirement.

### As-implemented surface (verified 2026-04-21)

- **Library:** `structlog>=25.0` configured via `structlog.stdlib.LoggerFactory` + `BoundLogger`. Stdlib bridge means existing `logging.getLogger(__name__)` call sites continue to work unchanged.
- **Bootstrap module:** `orchestrator/logging_config.py::configure_logging()` — idempotent, called once from `orchestrator/main.py` module body immediately after `load_dotenv()`. Fail-fast: any unknown `LOG_FORMAT` or invalid `LOG_LEVEL` raises `RuntimeError` at import time and the orchestrator refuses to boot.
- **Env surface (only two vars):** `LOG_FORMAT=console|json` (default `console`) and `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR|CRITICAL` (default `INFO`, case-insensitive).
- **Renderer-only difference between dev/prod:** `console` → `structlog.dev.ConsoleRenderer`; `json` → `structlog.processors.JSONRenderer`. Every other processor is identical.
- **Processor pipeline (in order):** `merge_contextvars` → `_bind_request_user` → `add_log_level` → `TimeStamper(iso, utc)` → `_redact` → `StackInfoRenderer` → `format_exc_info` → renderer.
- **Uvicorn integration:** `uvicorn`, `uvicorn.error`, `uvicorn.access` are reconfigured in the same `dictConfig` pass with `propagate=False` and the same `ProcessorFormatter` handler. Operators must NOT pass `--log-config` to uvicorn.
- **Request correlation:** single FastAPI middleware in `orchestrator/correlation.py` registered BEFORE `auth_middleware` (Starlette wraps in reverse, so `auth_middleware` becomes outermost). The middleware echoes `X-Request-ID` if present (after `.strip()`) or generates `uuid.uuid4().hex`, binds `request_id` into `structlog.contextvars`, stashes the live `Request` in a module-level `ContextVar`, and sets `X-Request-ID` on the response. The `_bind_request_user` processor reads `request.state.user.user_id` / `request.state.mcp_token_id` / `request.state.mcp_user_id` defensively at log time.
- **Audit cross-reference:** `actions/audit.write_audit()` mirrors a single `audit.executed` event per successful insert (`audit_id`, `user_id`, `action_name`, `connector`, `mode`, `is_reversible`) and `audit.write_failed` on DB error (`error`, `message` only). Payload bodies (`input_summary` / `result_summary` / `reverse_action` / `reverse_token`) NEVER appear in stdout. `permissions.log_action` is unchanged — DB-only, no stdout mirror.
- **Redaction:** recursive deny-list processor over the event_dict (case-insensitive substring match against `password`, `secret`, `token`, `api_key`, `authorization`, `cookie`, `jwt`, `bearer`); replaces sensitive values with the literal string `"<redacted>"`; pass-through for unknown leaf types; recurses into nested `dict` / `list` / `tuple`.
- **Redaction allowlist (in-flight discovery):** exact-match lowercase set `{user_id, mcp_user_id, mcp_token_id, request_id, audit_id}` exempted from the deny-list. Required because the deny-list keyword `token` would otherwise eat `mcp_token_id`, defeating the correlation purpose. New entries require a "never carries secret material" guarantee.
- **Test integration:** explicit `_logging_reset` autouse fixture in `orchestrator/tests/conftest.py` calls `logging_config.reset_for_tests()` before and after every test. NO production-code branches that detect pytest. Existing `caplog` compatibility preserved by the stdlib bridge; tests that need to assert structured event content use `structlog.testing.capture_logs()`.

### What was NOT changed (explicitly deferred)

- No Postgres schema migration: `audit_log.input_summary`, `audit_log.result_summary`, `action_log.input_summary`, `action_log.result_summary` remain `TEXT`.
- No reader / API contract changes for typed audit payloads.
- No full call-site rewrite of `_log = logging.getLogger(__name__)` → `log = structlog.get_logger(__name__)` (stdlib bridge keeps existing call sites working through the same processor pipeline).
- No event-name registry / canonical vocabulary lint.
- No OpenTelemetry / external aggregation / log-shipping integration.
- No `services/lumogis-graph` retrofit (separate Docker image with its own `sys.path` and `requirements.txt` — a shared-package refactor is required first; TODO marker placed in `services/lumogis-graph/main.py` pointing at `orchestrator/logging_config.py`).
- No `stack-control` retrofit.
- No `docker-compose.yml` default flip to `LOG_FORMAT=json` (operators set this themselves).
- No hardening of `auth_middleware` early-return 401 paths to also carry `request_id` (those branches log very little; clients can re-issue with their own `X-Request-ID`).

## Alternatives Considered

- **python-json-logger only** — Simpler but weaker context binding and processor-based redaction; see *(maintainer-local only; not part of the tracked repository)*.
- **Loguru** — Faster initial setup but poor fit for incremental adoption across a stdlib-logging codebase and multi-worker edge cases; rejected as the primary stack direction.
- **OpenTelemetry Logs as default** — Experimental Python logs SDK and optional collector complexity; defer as opt-in only.
- **PostgreSQL trigger-based generic row audit** — Duplicates semantic audit already implemented in Python; rejected.

## Consequences

- **Easier:** Operators gain grep-friendly JSON in Docker logs; developers can bind `request_id` / user context consistently; security reviews can correlate stdout incidents with `audit_log` rows via `audit_id`.
- **Harder:** Global logging configuration becomes load-order sensitive (`configure_logging()` MUST be the first non-stdlib call after `load_dotenv()`); teams must maintain an `event` vocabulary and redaction rules to avoid leaking secrets into JSON.
- **Future chunks must know:** New sensitive identifiers (e.g. additional MCP-related tokens) should appear in Postgres audit first; stdout logging only mirrors non-secret references. Any JSONB migration for summaries must preserve read paths for historical TEXT rows. Any new "safe identifier" key whose name collides with the deny-list (anything containing `token`, `secret`, `key`, `auth`, `cookie`, `jwt`, `bearer`) must be added to `_REDACT_ALLOWLIST` AND must be guaranteed to never carry secret material.

## Revisit conditions

- If **OpenTelemetry Python Logs** leave experimental status with stable semver guarantees **and** multi-service Lumogis deployments become the norm, revisit adding an **opt-in** OTLP profile documented in compose overrides (not default).
- If investigation workloads require **heavy JSON querying inside audit payloads**, revisit **JSONB columns** (or generated columns) on `audit_log` / `action_log` with explicit migration + UI contracts.
- If profiling shows logging as a **hot-path bottleneck** after structlog adoption, revisit **QueueHandler** / listener patterns before changing the logging library.
- If `services/lumogis-graph` (and/or `stack-control`) needs the same correlation story end-to-end, factor `logging_config.py` + `correlation.py` into a shared installable package and consume it from both services.

## Status history

- 2026-04-20: Draft created by /explore.
- 2026-04-21: Finalised by /verify-plan — implementation confirmed the decision (structlog + stdlib bridge with `ProcessorFormatter`, `LOG_FORMAT=console|json`, `LOG_LEVEL`, fail-fast bootstrap, recursive deny-list redaction with an explicit allowlist for `user_id` / `mcp_user_id` / `mcp_token_id` / `request_id` / `audit_id`, request-correlation middleware echoing/generating `X-Request-ID` and binding `request_id` + `user_id` + `mcp_token_id` + `mcp_user_id` from `request.state`, uvicorn triad routed through the same handler, `actions/audit.write_audit` mirroring `audit.executed` / `audit.write_failed` without payload bodies). Postgres `audit_log` / `action_log` summary columns remain `TEXT` — JSONB migration explicitly deferred and recorded in "Revisit conditions" above. One in-flight discovery added to the architectural record: a redaction allowlist is required to keep correlation ids visible (deny-list keyword `token` would otherwise eat `mcp_token_id`); the allowlist is exact-match lowercase only, and new entries require a "never carries secret material" guarantee.
