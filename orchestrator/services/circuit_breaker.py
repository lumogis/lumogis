# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Consecutive-failure circuit breaker (LUM-125).

Most retry loops in the codebase are already bounded by an attempt cap
(``batch_queue``, ``proposal_queue``, ``memory_purge``, the tool-chain cap in
:mod:`loop`, etc.). What was missing is protection against *cross-call*
runaway spend: when an expensive upstream (a cloud LLM, in particular) fails
repeatedly, every new request keeps paying to hit it. Claude Code's own
telemetry showed sessions racking up thousands of consecutive failures —
the fix is a breaker that stops calling after N consecutive failures and
fails fast for a cooldown window instead.

This module provides a small, thread-safe primitive plus a registry so the
same logical operation shares breaker state across call sites. It is wired
into the LLM provider boundary by :func:`config.get_llm_provider` via
:func:`wrap_llm_provider`; future expensive subsystems (consolidation,
autocompact) can reuse :func:`get_breaker` directly.

A circuit open is an **operational** event, so it is surfaced via structured
logging (``event=circuit_opened``) — the per-user ``audit_log`` table is for
user actions, not infra health.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

_log = logging.getLogger(__name__)

# Suggested consecutive-failure ceilings per operation class. cloud_llm matches
# Claude Code's MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES (3); local backends are a
# little more forgiving. Only ``cloud_llm`` is wired today (LLM provider);
# the rest document intent for future consumers.
MAX_CONSECUTIVE_FAILURES: dict[str, int] = {
    "cloud_llm": 3,
    "ollama": 5,
    "qdrant": 5,
    "falkordb": 5,
    "embedding": 3,
    "autocompact": 3,
}

_DEFAULT_COOLDOWN_S = 30.0


class CircuitOpenError(RuntimeError):
    """Raised by :meth:`CircuitBreaker.allow` when the circuit is open.

    Subclasses :class:`RuntimeError` so existing broad ``except Exception``
    handlers on the chat hot path map it to an HTTP 503 (service unavailable),
    which is the correct semantics when an upstream is being shielded.
    """


class CircuitBreaker:
    """Per-operation consecutive-failure breaker with a cooldown half-open probe.

    States: ``closed`` → normal; ``open`` → fail fast until the cooldown
    elapses; ``half_open`` → allow a single probe, which closes the circuit on
    success or re-opens it on failure.

    Thread-safe. ``now_fn`` is injectable so tests can advance the clock
    without sleeping.
    """

    def __init__(
        self,
        name: str,
        *,
        max_failures: int,
        cooldown_s: float = _DEFAULT_COOLDOWN_S,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_failures < 1:
            raise ValueError("max_failures must be >= 1")
        self.name = name
        self.max_failures = max_failures
        self.cooldown_s = cooldown_s
        self._now = now_fn
        self._lock = threading.Lock()
        self._state = "closed"
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        """Current state, resolving an elapsed cooldown to ``half_open``."""
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        # Caller holds the lock. Promote open→half_open once cooldown elapses.
        if self._state == "open" and self._opened_at is not None:
            if self._now() - self._opened_at >= self.cooldown_s:
                self._state = "half_open"

    def allow(self) -> None:
        """Raise :class:`CircuitOpenError` if the circuit is open (still cooling)."""
        with self._lock:
            self._maybe_half_open()
            if self._state == "open":
                raise CircuitOpenError(
                    f"circuit '{self.name}' is open "
                    f"({self._consecutive_failures} consecutive failures)"
                )
            # closed or half_open → allow the call (half_open lets one probe through)

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None
            self._state = "closed"

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            was_half_open = self._state == "half_open"
            if was_half_open or self._consecutive_failures >= self.max_failures:
                self._state = "open"
                self._opened_at = self._now()
                _log.warning(
                    "circuit breaker opened",
                    extra={
                        "event": "circuit_opened",
                        "operation": self.name,
                        "consecutive_failures": self._consecutive_failures,
                        "cooldown_s": self.cooldown_s,
                    },
                )

    def call(self, fn: Callable[[], object]) -> object:
        """Run ``fn`` under the breaker: fail fast if open, else record outcome."""
        self.allow()
        try:
            result = fn()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


# --- Registry ---------------------------------------------------------------

_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_breaker(
    name: str,
    *,
    max_failures: int,
    cooldown_s: float = _DEFAULT_COOLDOWN_S,
) -> CircuitBreaker:
    """Return the shared breaker for ``name``, creating it on first use.

    Parameters are honoured only at creation time — later calls return the
    existing breaker so its accumulated state is shared across call sites.
    """
    with _registry_lock:
        breaker = _breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(name, max_failures=max_failures, cooldown_s=cooldown_s)
            _breakers[name] = breaker
        return breaker


def reset_all() -> None:
    """Drop all registered breakers (test helper)."""
    with _registry_lock:
        _breakers.clear()


def snapshot() -> list[dict[str, object]]:
    """Current breaker states — for admin/diagnostics surfaces. No secrets."""
    with _registry_lock:
        breakers = list(_breakers.values())
    return [
        {
            "operation": b.name,
            "state": b.state,
            "max_failures": b.max_failures,
            "cooldown_s": b.cooldown_s,
        }
        for b in breakers
    ]


# --- LLM provider wrapping --------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def llm_circuit_enabled() -> bool:
    return _env_bool("LUMOGIS_LLM_CIRCUIT_ENABLED", True)


def _llm_max_failures() -> int:
    raw = os.environ.get("LUMOGIS_LLM_CIRCUIT_MAX_FAILURES")
    if raw is None:
        return MAX_CONSECUTIVE_FAILURES["cloud_llm"]
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return MAX_CONSECUTIVE_FAILURES["cloud_llm"]


def _llm_cooldown_s() -> float:
    raw = os.environ.get("LUMOGIS_LLM_CIRCUIT_COOLDOWN_S")
    if raw is None:
        return _DEFAULT_COOLDOWN_S
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_COOLDOWN_S


class CircuitBreakingLLMProvider:
    """LLMProvider decorator that fails fast when the model's circuit is open.

    Implements the :class:`ports.llm_provider.LLMProvider` protocol by
    delegating to ``inner`` and recording success/failure on the breaker.
    Streaming completion records success only when the stream finishes cleanly;
    an exception at call time or mid-stream counts as a failure.
    """

    def __init__(self, inner, breaker: CircuitBreaker) -> None:
        self._inner = inner
        self._breaker = breaker

    def chat(self, messages, tools=None, system=None, max_tokens=4096):
        from services.egress_guard import EgressBlockedError

        self._breaker.allow()
        try:
            response = self._inner.chat(messages, tools=tools, system=system, max_tokens=max_tokens)
        except Exception as exc:
            if not isinstance(exc, EgressBlockedError):
                self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return response

    def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
        from services.egress_guard import EgressBlockedError

        self._breaker.allow()
        failed = False
        try:
            for event in self._inner.chat_stream(
                messages, tools=tools, system=system, max_tokens=max_tokens
            ):
                yield event
        except Exception as exc:
            failed = True
            if not isinstance(exc, EgressBlockedError):
                self._breaker.record_failure()
            raise
        finally:
            # Consumers (loop.py) break on the ``end`` event without exhausting
            # this generator, which raises GeneratorExit — not Exception. Treat
            # any non-failing completion (including early consumer stop) as success.
            if not failed:
                self._breaker.record_success()


def wrap_llm_provider(inner, cache_key: str):
    """Wrap an LLM adapter in a circuit breaker keyed by its provider cache key.

    Returns ``inner`` unchanged when the breaker is disabled
    (``LUMOGIS_LLM_CIRCUIT_ENABLED=false``) so operators have an instant
    kill-switch. The breaker is keyed by ``cache_key`` (per user + model) so
    one user's failing model does not trip another's.
    """
    if not llm_circuit_enabled():
        return inner
    breaker = get_breaker(
        f"llm:{cache_key}",
        max_failures=_llm_max_failures(),
        cooldown_s=_llm_cooldown_s(),
    )
    return CircuitBreakingLLMProvider(inner, breaker)
