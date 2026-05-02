# Structured logging (orchestrator)

This is the short operator note for the structured-logging foundation
landed in the `structured_audit_logging` chunk
(*(maintainer-local only; not part of the tracked repository)*).

## Env vars

Two env vars (and only two) control logging:

| Var | Default | Values | Effect |
|-----|---------|--------|--------|
| `LOG_FORMAT` | `console` | `console` \| `json` | `console` renders colored human-readable lines via `structlog.dev.ConsoleRenderer` (intended for local dev / single-user installs). `json` renders one JSON object per line via `structlog.processors.JSONRenderer` (intended for Docker / production / log aggregation). |
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` | Standard stdlib level. Case-insensitive. Applies to every owned logger (root + the `uvicorn` triad). |

Both vars are validated at boot. An invalid value (e.g.
`LOG_FORMAT=garbage`) makes `configure_logging()` raise
`RuntimeError`, which propagates through uvicorn's app import — the
orchestrator refuses to start instead of silently degrading. This is
intentional (see plan D11).

Operators must NOT pass `--log-config` to uvicorn — `configure_logging()`
owns the `uvicorn`, `uvicorn.error`, and `uvicorn.access` loggers and
will be overridden by a CLI-supplied dictConfig.

## What you get on every line

Every emitted log line — whether from structlog (`structlog.get_logger(...)`)
or from a stdlib `logging.getLogger(...)` call site (the bulk of the
codebase) — flows through the same processor pipeline:

1. `structlog.contextvars.merge_contextvars` — pulls `request_id` from
   the per-request contextvar set by `correlation_middleware`.
2. `_bind_request_user` — pulls `user_id` / `mcp_token_id` /
   `mcp_user_id` from `request.state` at log time (i.e. after
   `auth_middleware` has populated them).
3. `add_log_level` — emits `"level"`.
4. `TimeStamper(fmt="iso", utc=True)` — emits `"timestamp"`.
5. `_redact` — recursively redacts sensitive keys (deny-list, see
   below).
6. `StackInfoRenderer` + `format_exc_info` — renders Python tracebacks
   when `exc_info=True` is passed.
7. The renderer chosen by `LOG_FORMAT`.

## Correlation

A single FastAPI middleware (`orchestrator/correlation.py::correlation_middleware`)
handles per-request correlation:

- **`X-Request-ID`** — echoed if the client provided one (after
  `.strip()`); otherwise a fresh `uuid.uuid4().hex` is generated. Bound
  into `structlog.contextvars` for the lifetime of the request.
- **Response header** — every response carries `X-Request-ID` so that
  log aggregators can cross-reference application logs with whatever
  the client / reverse proxy stored.
- **`user_id` / `mcp_token_id` / `mcp_user_id`** — bound at *log time*
  by reading the live `Request` from a contextvar. This means:
  - In single-user mode (`AUTH_ENABLED=false`), every line carries
    `user_id="default"` (the dev-mode default user).
  - In family-LAN mode, every line carries the JWT subject user_id.
  - On `/mcp/*`, the `mcp_token_id` and `mcp_user_id` set by
    `auth._check_mcp_bearer` are also bound when present.

### Known scope tradeoff

`auth_middleware` runs *outside* `correlation_middleware` (the latter
is registered first in code, which makes Starlette wrap auth
outermost). Log lines emitted *inside* `auth_middleware` itself
*before* it calls `await call_next(request)` (e.g. the early-return 401
paths) do NOT carry `request_id`. This is deliberate — it lets the
correlation middleware bind `request.state.user` without modifying
`auth.py`. Clients can still cross-reference 401s by passing their
own `X-Request-ID` on retry.

## Redaction

The `_redact` processor (`orchestrator/logging_config.py`) walks every
event_dict (recursively into nested `dict` / `list` / `tuple`) and
replaces the value of any key whose lowercase string contains any of:

```
password   secret   token   api_key   authorization   cookie   jwt   bearer
```

with the literal string `"<redacted>"`. This is a deny-list — the
goal is "never accidentally print a secret", not "guarantee schema
correctness". Pass-through for unknown leaf types (so the processor
cannot crash a log call on an exotic value).

### Redaction allowlist

A short exact-match (lowercase) allowlist exempts well-known correlation
/ identifier keys whose names happen to collide with deny-list
substrings:

```
user_id   mcp_user_id   mcp_token_id   request_id   audit_id
```

These are DB row ids / correlation ids — never bearer secrets — and
are deliberately bound by `correlation_middleware` and
`actions.audit.write_audit` for investigator-friendly cross-referencing.
Without the allowlist, `mcp_token_id` would match the `token` keyword
and become `<redacted>`, defeating the correlation purpose. New keys
should only be added to the allowlist if they are guaranteed to never
carry secret material — when in doubt, leave the deny-list redaction
in place and use a different field name for the safe identifier.

## Audit cross-reference

`orchestrator/actions/audit.py::write_audit()` mirrors every
successful audit row to stdout as a single structured event:

```json
{
  "event": "audit.executed",
  "audit_id": 1234,
  "user_id": "alice",
  "action_name": "filesystem-mcp.write_note",
  "connector": "filesystem-mcp",
  "mode": "DO",
  "is_reversible": true,
  "request_id": "...",
  "level": "info",
  "timestamp": "2026-04-21T12:34:56Z"
}
```

`audit_id` is the primary key in the Postgres `audit_log` table, so a
log aggregator can pivot from a stdout line to the full DB row (with
`input_summary` / `result_summary` / `reverse_action`) and back.

`permissions.log_action()` (the per-tool-call permission audit row)
intentionally does NOT mirror to stdout — it would dwarf application
logs in volume (every tool call goes through it). Investigators read
those rows from `action_log` directly.

On audit-write failure, `write_audit` emits a single
`audit.write_failed` event with `error` (exception class name) and
`message`, but no payload bodies.

## Intentionally deferred

The following are explicitly NOT part of this chunk and will land in
follow-ups:

- Migrating `audit_log.input_summary` / `audit_log.result_summary` /
  `action_log.input_summary` / `action_log.result_summary` from `TEXT`
  to `JSONB`.
- API/reader contract changes for typed audit payloads.
- A canonical `event` vocabulary registry / linter.
- Full call-site migration of every `_log = logging.getLogger(__name__)`
  to `log = structlog.get_logger(__name__)` with named events.
- OpenTelemetry / external log shipping / collector integration.
- Applying the same bootstrap to `services/lumogis-graph` (separate
  Docker image with its own `sys.path` and `requirements.txt` — a
  shared-package refactor is required first).
- Applying the same bootstrap to `stack-control`.
- Defaulting `LOG_FORMAT=json` in `docker-compose.yml` (operators set
  this themselves today).

## Tests

Tests use an explicit pytest fixture, NOT a production-code branch
that detects pytest. The autouse fixture lives in
`orchestrator/tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _logging_reset():
    from logging_config import reset_for_tests
    reset_for_tests()
    yield
    reset_for_tests()
```

`reset_for_tests()` clears `structlog.contextvars`, drops handlers
attached to owned loggers, and re-runs `configure_logging()` with
`LOG_FORMAT=console` / `LOG_LEVEL=DEBUG`. Existing pytest `caplog`
compatibility is preserved by the stdlib bridge (every structlog event
flows through a stdlib LogRecord, which is what `caplog` captures).

Tests that want to assert on structured event content use
`structlog.testing.capture_logs()` as a context manager.
