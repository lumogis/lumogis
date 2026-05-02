# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Structured operational logging bootstrap (chunk: structured_audit_logging).

Public surface
--------------

* :func:`configure_logging` — call exactly once at orchestrator startup
  (from ``main.py`` module body, immediately after ``load_dotenv()``).
  Idempotent: subsequent calls tear down previously-attached handlers
  and rebuild from the current environment.

* :func:`reset_for_tests` — pytest helper that clears
  ``structlog.contextvars`` and re-runs ``configure_logging()`` with
  ``LOG_FORMAT=console`` / ``LOG_LEVEL=DEBUG``. Used by the autouse
  ``_logging_reset`` fixture in ``orchestrator/tests/conftest.py``.

Design (per plan D1, D5, D8, D9, D10, D11)
------------------------------------------

* **structlog with the stdlib bridge.** ``logging.getLogger(__name__)``
  call sites elsewhere in the codebase continue to work unchanged
  (handlers + levels + propagation are shared).

* **One processor pipeline.** ``shared_processors`` runs on every event
  (structlog-native via ``structlog.configure(processors=...)``,
  stdlib-foreign via ``ProcessorFormatter(foreign_pre_chain=...)``) so
  ``request_id`` / ``user_id`` / ``mcp_token_id`` correlation and
  redaction apply uniformly.

* **Renderer is the ONLY thing that depends on ``LOG_FORMAT``.** Every
  other processor is identical between dev and prod; only the final
  serialisation differs (colored ``ConsoleRenderer`` vs one
  JSON-object-per-line ``JSONRenderer``).

* **uvicorn is reconfigured in the same dictConfig pass.** The
  ``uvicorn`` / ``uvicorn.error`` / ``uvicorn.access`` loggers get the
  same handler with ``propagate=False``. Operators must NOT pass
  ``--log-config`` to uvicorn (documented in
  ``docs/structured-logging.md``).

* **Fail-fast.** ``configure_logging`` raises ``RuntimeError`` for any
  unknown ``LOG_FORMAT`` or invalid ``LOG_LEVEL`` so a misconfigured
  deployment refuses to boot rather than silently degrading.
"""

from __future__ import annotations

import logging
import logging.config
import os
from typing import Any

import structlog
from correlation import _REQUEST_CTXVAR

# Keys whose values must NEVER appear in stdout logs. Match is
# case-insensitive substring against ``str(key).lower()``; if any of
# these substrings appear anywhere in the key, the value is replaced
# with the literal string ``"<redacted>"``. See plan D8.
_REDACT_KEYWORDS: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "cookie",
    "jwt",
    "bearer",
)

_REDACTED = "<redacted>"

# Exact-match (lowercase) allowlist for well-known identifier keys that
# would otherwise collide with the deny-list substring rules above
# (e.g. ``mcp_token_id`` contains ``token``; ``api_key_id`` would
# contain ``api_key``). These are DB row ids / correlation ids — NEVER
# bearer secrets — and are deliberately bound by
# ``correlation_middleware`` and ``actions.audit.write_audit`` for
# investigator-friendly cross-referencing. Resolves the explicit
# tension between plan Q3 ("bind mcp_token_id for correlation") and
# plan Q7 ("redact any key containing 'token'"). See
# ``docs/structured-logging.md`` § Redaction.
_REDACT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "user_id",
        "mcp_user_id",
        "mcp_token_id",
        "request_id",
        "audit_id",
    }
)

# Loggers we explicitly own. ``""`` is the root, the rest are uvicorn's
# triad. Anything else propagates through root.
_OWNED_LOGGERS: tuple[str, ...] = ("", "uvicorn", "uvicorn.error", "uvicorn.access")

# Module-level guard so re-entry tears down the previous configuration
# instead of stacking handlers on top of it.
_configured: bool = False


def _is_sensitive_key(key: Any) -> bool:
    """Return True if ``key`` is on the deny list and not on the allowlist.

    The deny list is a case-insensitive substring match against
    ``_REDACT_KEYWORDS``; the allowlist is an exact-match (lowercase)
    set of known-safe identifier keys (see ``_REDACT_ALLOWLIST``).
    """
    try:
        lowered = str(key).lower()
    except Exception:
        return False
    if lowered in _REDACT_ALLOWLIST:
        return False
    return any(kw in lowered for kw in _REDACT_KEYWORDS)


def _redact_value(value: Any) -> Any:
    """Recursively redact a value once we already know its key is sensitive."""
    return _REDACTED if not isinstance(value, (dict, list, tuple)) else _redact_walk(value)


def _redact_walk(node: Any) -> Any:
    """Walk a nested ``dict`` / ``list`` / ``tuple`` and redact sensitive keys.

    Pass-through for unknown leaf types so the processor never crashes
    a log call on an exotic value.
    """
    if isinstance(node, dict):
        out: dict = {}
        for k, v in node.items():
            if _is_sensitive_key(k):
                out[k] = _redact_value(v)
            elif isinstance(v, (dict, list, tuple)):
                out[k] = _redact_walk(v)
            else:
                out[k] = v
        return out
    if isinstance(node, list):
        return [_redact_walk(v) if isinstance(v, (dict, list, tuple)) else v for v in node]
    if isinstance(node, tuple):
        return tuple(_redact_walk(v) if isinstance(v, (dict, list, tuple)) else v for v in node)
    return node


def _redact(logger, method_name, event_dict):
    """structlog processor: deny-list redaction over the event_dict."""
    if not isinstance(event_dict, dict):
        return event_dict
    out: dict = {}
    for k, v in event_dict.items():
        if _is_sensitive_key(k):
            out[k] = _redact_value(v)
        elif isinstance(v, (dict, list, tuple)):
            out[k] = _redact_walk(v)
        else:
            out[k] = v
    return out


def _bind_request_user(logger, method_name, event_dict):
    """structlog processor: pull user_id / mcp_token_id from request.state at log time.

    The current ``Request`` is read from ``correlation._REQUEST_CTXVAR``,
    set by ``correlation_middleware``. We use ``setdefault`` so that an
    explicit ``log.info("event", user_id=...)`` call wins over the
    inferred value.
    """
    req = _REQUEST_CTXVAR.get()
    if req is None:
        return event_dict
    state = getattr(req, "state", None)
    if state is None:
        return event_dict
    user = getattr(state, "user", None)
    if user is not None:
        uid = getattr(user, "user_id", None)
        if uid:
            event_dict.setdefault("user_id", uid)
    token_id = getattr(state, "mcp_token_id", None)
    if token_id:
        event_dict.setdefault("mcp_token_id", token_id)
    mcp_user = getattr(state, "mcp_user_id", None)
    if mcp_user:
        event_dict.setdefault("mcp_user_id", mcp_user)
    return event_dict


def _shared_processors() -> list:
    """The processor chain that runs on every event (structlog + stdlib)."""
    return [
        structlog.contextvars.merge_contextvars,
        _bind_request_user,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def _resolve_log_level(raw: str) -> int:
    """Parse a stdlib level name. Raise RuntimeError on garbage."""
    name = (raw or "INFO").strip().upper()
    level = logging.getLevelName(name)
    # ``logging.getLevelName`` returns the string ``"Level NAME"`` for
    # unknown names rather than raising — unwrap that into a real error.
    if not isinstance(level, int):
        raise RuntimeError(
            f"LOG_LEVEL={raw!r} is not a valid logging level "
            "(expected DEBUG / INFO / WARNING / ERROR / CRITICAL)."
        )
    return level


def _resolve_renderer(raw: str):
    """Pick the renderer for the given LOG_FORMAT. Raise RuntimeError on garbage."""
    fmt = (raw or "console").strip().lower()
    if fmt == "console":
        return structlog.dev.ConsoleRenderer()
    if fmt == "json":
        return structlog.processors.JSONRenderer()
    raise RuntimeError(
        f"LOG_FORMAT={raw!r} is not a valid logging format (expected 'console' or 'json')."
    )


def _detach_owned_handlers() -> None:
    """Remove any handlers we previously attached to owned loggers."""
    for name in _OWNED_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)


def configure_logging() -> None:
    """Bootstrap structured logging. Idempotent. Fail-fast on misconfig.

    Reads ``LOG_FORMAT`` (``console`` | ``json``, default ``console``)
    and ``LOG_LEVEL`` (default ``INFO``) from the process environment.
    """
    global _configured

    log_format_raw = os.environ.get("LOG_FORMAT", "console")
    log_level_raw = os.environ.get("LOG_LEVEL", "INFO")

    # Validate first — fail-fast (D11) before mutating any handler state.
    renderer = _resolve_renderer(log_format_raw)
    log_level = _resolve_log_level(log_level_raw)

    if _configured:
        _detach_owned_handlers()

    shared = _shared_processors()

    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": structlog.stdlib.ProcessorFormatter,
                # ``processors`` runs only on events that already came
                # through structlog (i.e. ``wrap_for_formatter`` left a
                # ``_record``-wrapped event_dict on the LogRecord).
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    renderer,
                ],
                # ``foreign_pre_chain`` runs the same ``shared``
                # processors on stdlib-foreign records (uvicorn etc) so
                # the redaction / contextvars / timestamps story is
                # uniform across both.
                "foreign_pre_chain": shared,
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "structured",
            }
        },
        "loggers": {
            "": {
                "handlers": ["default"],
                "level": log_level,
                "propagate": True,
            },
            "uvicorn": {
                "handlers": ["default"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["default"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["default"],
                "level": log_level,
                "propagate": False,
            },
        },
    }

    try:
        logging.config.dictConfig(config)
    except Exception as exc:
        raise RuntimeError(
            f"configure_logging(): dictConfig failed ({type(exc).__name__}: {exc})"
        ) from exc

    structlog.configure(
        processors=shared
        + [
            # ``wrap_for_formatter`` is the bridge: it bundles the
            # event_dict onto the stdlib LogRecord so the
            # ``ProcessorFormatter`` on the handler can render it.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # ``cache_logger_on_first_use=False`` — required for
        # ``structlog.testing.capture_logs()`` to intercept events from
        # module-level ``structlog.get_logger(...)`` references (the
        # cache pins the wrapper to the FIRST configuration seen, so a
        # later ``capture_logs()`` reconfigure cannot reach it). The
        # per-call cost of resolving the wrapper is negligible vs DB /
        # network work in any code path that actually logs.
        cache_logger_on_first_use=False,
    )

    _configured = True


def reset_for_tests() -> None:
    """Pytest helper: drop all structlog state and reconfigure cleanly.

    Used by the autouse ``_logging_reset`` fixture in
    ``orchestrator/tests/conftest.py``. Production code MUST NOT call
    this — see plan D10.
    """
    global _configured

    structlog.contextvars.clear_contextvars()
    # Tests may have replaced the configuration with their own; reset
    # structlog's defaults so the next ``configure_logging`` rebuilds
    # from a known baseline.
    structlog.reset_defaults()
    _detach_owned_handlers()
    _configured = False

    # Force a known-good baseline regardless of what the test process
    # inherited from its parent.
    os.environ["LOG_FORMAT"] = "console"
    os.environ["LOG_LEVEL"] = "DEBUG"
    configure_logging()


__all__ = (
    "configure_logging",
    "reset_for_tests",
    "_REDACTED",
    "_REDACT_KEYWORDS",
    "_REDACT_ALLOWLIST",
)
