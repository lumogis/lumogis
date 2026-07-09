# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Circuit breaker primitive + LLM provider wrapping (LUM-125)."""

from __future__ import annotations

import pytest

from services import circuit_breaker as cb


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture(autouse=True)
def _clear_registry():
    cb.reset_all()
    yield
    cb.reset_all()


def _breaker(max_failures=3, cooldown_s=30.0, clock=None):
    return cb.CircuitBreaker(
        "test", max_failures=max_failures, cooldown_s=cooldown_s, now_fn=clock or (lambda: 0.0)
    )


def test_closed_allows_until_threshold():
    b = _breaker(max_failures=3)
    for _ in range(2):
        b.allow()  # no raise
        b.record_failure()
    assert b.state == "closed"  # 2 < 3
    b.allow()
    b.record_failure()  # 3rd consecutive → open
    assert b.state == "open"


def test_open_fails_fast():
    b = _breaker(max_failures=1)
    b.record_failure()
    assert b.state == "open"
    with pytest.raises(cb.CircuitOpenError):
        b.allow()


def test_success_resets_failure_streak():
    b = _breaker(max_failures=3)
    b.record_failure()
    b.record_failure()
    b.record_success()  # streak reset
    b.record_failure()
    b.record_failure()
    assert b.state == "closed"  # only 2 since reset


def test_cooldown_to_half_open_then_close():
    clock = FakeClock()
    b = _breaker(max_failures=1, cooldown_s=30.0, clock=clock)
    b.record_failure()
    assert b.state == "open"
    with pytest.raises(cb.CircuitOpenError):
        b.allow()
    clock.advance(30.0)
    assert b.state == "half_open"
    b.allow()  # probe allowed
    b.record_success()
    assert b.state == "closed"


def test_half_open_failure_reopens():
    clock = FakeClock()
    b = _breaker(max_failures=1, cooldown_s=10.0, clock=clock)
    b.record_failure()
    clock.advance(10.0)
    assert b.state == "half_open"
    b.allow()
    b.record_failure()  # probe failed → reopen
    assert b.state == "open"
    with pytest.raises(cb.CircuitOpenError):
        b.allow()


def test_call_helper_records_outcome():
    b = _breaker(max_failures=2)

    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        b.call(boom)
    with pytest.raises(ValueError):
        b.call(boom)
    assert b.state == "open"
    with pytest.raises(cb.CircuitOpenError):
        b.call(lambda: 1)  # fails fast, boom never runs


def test_max_failures_must_be_positive():
    with pytest.raises(ValueError):
        cb.CircuitBreaker("x", max_failures=0)


def test_registry_shares_state():
    b1 = cb.get_breaker("op", max_failures=2)
    b2 = cb.get_breaker("op", max_failures=99)  # params ignored after creation
    assert b1 is b2
    assert b1.max_failures == 2


def test_snapshot_shape_no_secrets():
    cb.get_breaker("llm:user:model", max_failures=3)
    snap = cb.snapshot()
    assert snap and snap[0]["operation"] == "llm:user:model"
    assert set(snap[0]) == {"operation", "state", "max_failures", "cooldown_s"}


# --- LLM provider wrapping --------------------------------------------------


class FakeProvider:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.chat_calls = 0
        self.stream_calls = 0

    def chat(self, messages, tools=None, system=None, max_tokens=4096):
        self.chat_calls += 1
        if self.fail:
            raise RuntimeError("upstream down")
        return "ok"

    def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
        self.stream_calls += 1
        if self.fail:
            raise RuntimeError("upstream down")
        yield "chunk"


def test_wrap_disabled_returns_inner(monkeypatch):
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_ENABLED", "false")
    inner = FakeProvider()
    assert cb.wrap_llm_provider(inner, "u:m") is inner


def test_wrap_enabled_opens_then_fails_fast(monkeypatch):
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_MAX_FAILURES", "2")
    inner = FakeProvider(fail=True)
    wrapped = cb.wrap_llm_provider(inner, "u:m")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            wrapped.chat([{"role": "user", "content": "hi"}])
    assert inner.chat_calls == 2
    # Circuit now open — next call fails fast without touching the upstream.
    with pytest.raises(cb.CircuitOpenError):
        wrapped.chat([{"role": "user", "content": "hi"}])
    assert inner.chat_calls == 2  # not incremented


def test_wrap_success_keeps_closed(monkeypatch):
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_MAX_FAILURES", "2")
    inner = FakeProvider(fail=False)
    wrapped = cb.wrap_llm_provider(inner, "u:m")
    assert wrapped.chat([{"role": "user", "content": "hi"}]) == "ok"
    breaker = cb.get_breaker("llm:u:m", max_failures=2)
    assert breaker.state == "closed"


def test_wrap_stream_clean_completion_records_success(monkeypatch):
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_ENABLED", "true")
    inner = FakeProvider(fail=False)
    wrapped = cb.wrap_llm_provider(inner, "u:m")
    chunks = list(wrapped.chat_stream([{"role": "user", "content": "hi"}]))
    assert chunks == ["chunk"]
    assert cb.get_breaker("llm:u:m", max_failures=3).state == "closed"


def test_wrap_stream_early_break_on_end_records_success(monkeypatch):
    """loop.py breaks on ``end`` without exhausting chat_stream — streak must reset."""

    class _Event:
        def __init__(self, type: str) -> None:
            self.type = type

    class _StreamProvider:
        def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
            yield _Event("text")
            yield _Event("end")
            yield _Event("text")  # never consumed when breaking on end

    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_MAX_FAILURES", "3")
    wrapped = cb.wrap_llm_provider(_StreamProvider(), "u:m")
    breaker = cb.get_breaker("llm:u:m", max_failures=3)
    breaker.record_failure()
    breaker.record_failure()

    for event in wrapped.chat_stream([{"role": "user", "content": "hi"}]):
        if event.type == "end":
            break

    # Success should reset the streak — two more failures stay below threshold.
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed"


def test_wrap_stream_failure_opens(monkeypatch):
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_MAX_FAILURES", "1")
    inner = FakeProvider(fail=True)
    wrapped = cb.wrap_llm_provider(inner, "u:m")
    with pytest.raises(RuntimeError):
        list(wrapped.chat_stream([{"role": "user", "content": "hi"}]))
    assert cb.get_breaker("llm:u:m", max_failures=1).state == "open"
