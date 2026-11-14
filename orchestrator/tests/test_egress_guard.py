# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for egress guard allowlist + wrapper (LUM-553)."""

from __future__ import annotations

import contextvars
import logging
import socket
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from models.llm import LLMEvent
from services.privacy_mode import PrivacyModeBlocked

import config
from services import egress_guard as eg


class _PrivacyFakeStore:
    def __init__(self) -> None:
        self.app_settings: dict[str, str] = {}
        self.privacy_user: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.lower().split())
        params = params or ()
        if "insert into app_settings" in q or "on conflict" in q:
            self.app_settings[params[0]] = params[1]
        elif "delete from privacy_user_settings" in q:
            self.privacy_user.pop(params[0], None)
        elif "insert into privacy_user_settings" in q:
            self.privacy_user[params[0]] = params[1]

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.lower().split())
        params = params or ()
        if "from app_settings" in q:
            v = self.app_settings.get(params[0])
            return {"value": v} if v is not None else None
        if "from privacy_user_settings" in q:
            r = self.privacy_user.get(params[0])
            return {"restriction": r} if r else None
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list:
        return []

    def close(self) -> None:
        pass

    @contextmanager
    def transaction(self):
        yield


_FAKE_MODELS = {
    "claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
    "chatgpt": {"adapter": "openai", "api_key_env": "OPENAI_API_KEY"},
    "llama": {"adapter": "openai", "base_url": "http://ollama:11434/v1"},
}


@pytest.fixture
def egress_env(monkeypatch):
    store = _PrivacyFakeStore()
    monkeypatch.setitem(config._instances, "metadata_store", store)
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(_FAKE_MODELS))
    monkeypatch.setattr(config, "get_model_config", lambda name: dict(_FAKE_MODELS[name]))
    monkeypatch.setattr(
        config,
        "is_local_model",
        lambda name: "ollama" in (_FAKE_MODELS.get(name, {}).get("base_url") or "").lower(),
    )
    monkeypatch.setattr("auth.auth_enabled", lambda: False)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    eg._dynamic_ollama_cache = None
    eg._dynamic_ollama_cached_at = 0.0
    yield store


def test_egress_guard_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_EGRESS_GUARD", raising=False)
    assert eg.egress_guard_enabled() is False


def test_allowlist_includes_postgres_qdrant_ollama(egress_env, monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_EGRESS_GUARD", raising=False)
    hosts = eg.build_allowlist(user_id=None, for_ceiling=False)
    assert "postgres" in hosts
    assert "qdrant" in hosts
    assert "ollama" in hosts


def test_allowlist_excludes_cloud_when_local_only(egress_env):
    hosts = eg.build_allowlist(user_id="default", for_ceiling=False)
    assert "api.anthropic.com" not in hosts
    assert "api.openai.com" not in hosts


def test_allowlist_includes_cloud_when_allow_cloud(egress_env):
    egress_env.app_settings["privacy_mode"] = "allow_cloud"
    hosts = eg.build_allowlist(user_id=None, for_ceiling=False)
    assert "api.anthropic.com" in hosts
    assert "api.openai.com" in hosts


def test_local_model_behind_a_proxy_has_its_proxy_allowlisted(egress_env, monkeypatch):
    """LUM-766: the allowlist must contain what the dialer actually dials.

    `get_llm_provider` passes `base_url=proxy or base_url` to the adapter with no
    local/cloud distinction (config.py:481,504), so a model configured with a
    `proxy_url` never opens a socket to its own `base_url`. Harvesting only
    `base_url` for local models left the guard blocking a legitimately-local model
    reached through a proxy — a self-block, and the same defect LUM-638 fixed for
    IMAP.

    `llama` is the realistic shape: the OpenAI-compatible adapter pointed at
    Ollama, which is what makes `is_local_model` true while the openai adapter
    honours `proxy_url`.
    """
    models = dict(_FAKE_MODELS)
    models["llama"] = {**models["llama"], "proxy_url": "http://corp-egress-proxy:3128"}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))

    hosts = eg.build_allowlist(user_id="default", for_ceiling=False)

    assert "corp-egress-proxy" in hosts, (
        "a local model's proxy host must be allowlisted — the adapter dials it, "
        f"not base_url; got: {sorted(hosts)}"
    )
    # Still there: `proxy_url` overrides base_url at dial time, but the allowlist
    # is a union over models and ollama remains reachable for the others.
    assert "ollama" in hosts


def test_local_proxy_is_allowlisted_even_when_cloud_is_blocked(egress_env, monkeypatch):
    """The gate that broke it: local-only is where a corporate proxy lives.

    The cloud branch harvests `proxy_url` only when cloud is allowed. Applying that
    same gate to local models would re-break this case on exactly the deployments
    most likely to hit it, so the local harvest is deliberately ungated — while
    cloud *models* stay excluded, which is what the privacy mode actually promises.
    """
    models = dict(_FAKE_MODELS)
    models["llama"] = {**models["llama"], "proxy_url": "http://corp-egress-proxy:3128"}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))

    hosts = eg.build_allowlist(user_id="default", for_ceiling=False)  # local-only

    assert "corp-egress-proxy" in hosts
    assert "api.anthropic.com" not in hosts, "local-only must still exclude cloud models"
    assert "api.openai.com" not in hosts


def test_a_cloud_proxy_on_a_local_model_is_allowlisted_documented_residual(egress_env, monkeypatch):
    """Pins a deliberate trade-off so it stays visible rather than becoming folklore.

    `is_local_model` is a substring test for "ollama" in `base_url`, so an operator
    CAN mark a model local while pointing `proxy_url` at a cloud host — and that
    host is then allowlisted even under local-only. This asserts the current
    behaviour, not an endorsement of it: the configuration is self-contradictory
    and operator-authored, and the place to refuse it is the privacy /
    model-enablement path, not a silent omission in the allowlist that surfaces as
    an unexplained connection error.

    If that judgement is ever revisited, this test is the thing that should fail.
    """
    models = dict(_FAKE_MODELS)
    models["llama"] = {**models["llama"], "proxy_url": "https://api.openai.com"}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))

    hosts = eg.build_allowlist(user_id="default", for_ceiling=False)  # local-only

    assert "api.openai.com" in hosts


def test_wrap_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_EGRESS_GUARD", raising=False)

    class _Inner:
        pass

    inner = _Inner()
    assert eg.wrap_llm_provider_egress(inner, user_id="alice") is inner


def test_dynamic_ollama_hosts_cached(egress_env, monkeypatch):
    calls = {"n": 0}

    def _fake_dynamic():
        calls["n"] += 1
        return {"dyn": {"base_url": "http://ollama:11434/v1"}}

    monkeypatch.setattr(config, "_dynamic_ollama_models", _fake_dynamic)
    eg._dynamic_ollama_cache = None
    eg._dynamic_ollama_cached_at = 0.0
    eg.build_allowlist(user_id=None, for_ceiling=False)
    eg.build_allowlist(user_id=None, for_ceiling=False)
    assert calls["n"] == 1


def test_egress_blocked_not_counted_as_circuit_failure(monkeypatch):
    from services import circuit_breaker as cb

    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_MAX_FAILURES", "3")
    cb.reset_all()

    class _Inner:
        def chat(self, messages, tools=None, system=None, max_tokens=4096):
            raise eg.EgressBlockedError(host="evil.example")

        def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
            raise eg.EgressBlockedError(host="evil.example")
            yield  # pragma: no cover

    wrapped = cb.wrap_llm_provider(_Inner(), "u:m")
    breaker = cb.get_breaker("llm:u:m", max_failures=3)
    for _ in range(3):
        with pytest.raises(eg.EgressBlockedError):
            wrapped.chat([{"role": "user", "content": "hi"}])
    assert breaker.state == "closed"


def test_privacy_still_blocks_before_egress(egress_env, monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    with patch("services.privacy_mode.record_privacy_block"):
        with pytest.raises(PrivacyModeBlocked):
            config.get_llm_provider("claude", user_id="default")


def test_sdk_wrapped_egress_block_translated(egress_env, monkeypatch):
    tethered = pytest.importorskip("tethered")
    from unittest.mock import Mock

    from openai import APIConnectionError

    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")

    class _Inner:
        def chat(self, messages, tools=None, system=None, max_tokens=4096):
            blocked = tethered.EgressBlocked(host="evil.example", port=443)
            raise APIConnectionError(
                message="connection failed",
                request=Mock(),
            ) from blocked

        def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
            yield  # pragma: no cover

    wrapped = eg.wrap_llm_provider_egress(_Inner(), user_id="alice")
    with pytest.raises(eg.EgressBlockedError) as excinfo:
        wrapped.chat([{"role": "user", "content": "hi"}])
    assert excinfo.value.host == "evil.example"


def test_scope_disables_tethered_localhost_subnet_exemption(egress_env, monkeypatch):
    tethered = pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")

    captured: dict = {}
    real_scope = tethered.scope

    @contextmanager
    def _spy_scope(*args, **kwargs):
        captured.update(kwargs)
        with real_scope(*args, **kwargs):
            yield

    monkeypatch.setattr(tethered, "scope", _spy_scope)

    class _Inner:
        def chat(self, messages, tools=None, system=None, max_tokens=4096):
            return {"ok": True}

        def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
            yield {"ok": True}

    wrapped = eg.wrap_llm_provider_egress(_Inner(), user_id="alice")
    wrapped.chat([{"role": "user", "content": "hi"}])
    assert captured.get("allow_localhost") is False


# ---------------------------------------------------------------------------
# LUM-909: chat_stream must not hold the tethered scope across ``yield``.
#
# tethered 1.x scopes live on a ContextVar stack. Starlette steps a sync
# StreamingResponse iterator with ``anyio.to_thread.run_sync``, which runs each
# ``next()`` in a fresh copy of the context; ``_step_in_fresh_contexts``
# reproduces that. 127.0.0.2 is loopback (nothing leaves the box, and the
# test-runner's ``--network none`` is fine) but is not in the allowlist, and the
# scope sets ``allow_localhost=False`` (LUM-570), so it stands in for a blocked
# host.
# ---------------------------------------------------------------------------

_CONTEXT_EXIT_WARNING = "was exited in a context that did not enter it"


@pytest.fixture
def blocked_listener():
    """A listening socket on 127.0.0.2: reachable, but outside the allowlist."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.2", 0))
    except OSError:  # pragma: no cover - all of 127.0.0.0/8 is on lo on Linux
        srv.close()
        pytest.skip("cannot bind 127.0.0.2 on this host")
    srv.listen(8)
    try:
        yield srv.getsockname()
    finally:
        srv.close()


def _connect(addr) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.connect(addr)
    finally:
        sock.close()


class _LaterChunkConnects:
    """Adapter stand-in: the first chunk is free, the second dials ``addr``."""

    def __init__(self, addr) -> None:
        self._addr = addr

    def chat(self, messages, tools=None, system=None, max_tokens=4096):  # pragma: no cover
        raise AssertionError("not used")

    def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
        yield LLMEvent(type="text", content="first")
        _connect(self._addr)
        yield LLMEvent(type="text", content="second")
        yield LLMEvent(type="end", stop_reason="stop")


def _step_in_fresh_contexts(gen):
    """Drive ``gen`` the way Starlette's ``iterate_in_threadpool`` does."""
    while True:
        try:
            yield contextvars.copy_context().run(next, gen)
        except StopIteration:
            return


@pytest.mark.parametrize("stepping", ["same_context", "fresh_context_per_step"])
def test_chat_stream_blocks_a_connect_on_a_later_chunk(
    egress_env, monkeypatch, blocked_listener, stepping
):
    pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    wrapped = eg.wrap_llm_provider_egress(_LaterChunkConnects(blocked_listener), user_id="alice")
    gen = wrapped.chat_stream([{"role": "user", "content": "hi"}])
    events = iter(gen) if stepping == "same_context" else _step_in_fresh_contexts(gen)

    assert next(events).content == "first"
    with pytest.raises(eg.EgressBlockedError) as excinfo:
        next(events)
    assert excinfo.value.host == "127.0.0.2"


def test_chat_stream_holds_no_scope_in_the_consumer_between_chunks(
    egress_env, monkeypatch, blocked_listener
):
    pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")

    class _Inner:
        def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
            yield "a"
            yield "b"

    wrapped = eg.wrap_llm_provider_egress(_Inner(), user_id="alice")
    seen = []
    for chunk in wrapped.chat_stream([{"role": "user", "content": "hi"}]):
        seen.append(chunk)
        # The consumer's own code. A scope left open across the yield would
        # make the guard refuse this connect.
        _connect(blocked_listener)
    assert seen == ["a", "b"]


@pytest.mark.parametrize("early_close", [False, True])
def test_chat_stream_emits_no_tethered_context_warning(
    egress_env, monkeypatch, caplog, early_close
):
    pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")

    class _Inner:
        def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
            yield "a"
            yield "b"
            yield "c"

    wrapped = eg.wrap_llm_provider_egress(_Inner(), user_id="alice")
    gen = wrapped.chat_stream([{"role": "user", "content": "hi"}])
    # tethered reports this through ``logging`` (logger "tethered"), not the
    # ``warnings`` module, so caplog rather than pytest.warns.
    with caplog.at_level(logging.WARNING, logger="tethered"):
        seen = []
        for chunk in _step_in_fresh_contexts(gen):
            seen.append(chunk)
            if early_close:
                break
        contextvars.copy_context().run(gen.close)
    assert seen == (["a"] if early_close else ["a", "b", "c"])
    assert not [r for r in caplog.records if _CONTEXT_EXIT_WARNING in r.getMessage()]


def test_ask_stream_turns_a_later_chunk_egress_block_into_an_sse_error(
    egress_env, monkeypatch, blocked_listener
):
    """ADR-153: in-band SSE error via ``_friendly_error``, and no breaker increment."""
    pytest.importorskip("tethered")
    import loop

    from services import circuit_breaker as cb

    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_MAX_FAILURES", "1")
    cb.reset_all()
    provider = cb.wrap_llm_provider(
        eg.wrap_llm_provider_egress(_LaterChunkConnects(blocked_listener), user_id="alice"),
        "alice:llama",
    )
    monkeypatch.setattr("services.privacy_mode.resolve_model_for_request", lambda m, u: (m, None))
    monkeypatch.setattr(config, "get_llm_provider", lambda *_a, **_k: provider)

    events = list(
        _step_in_fresh_contexts(
            loop.ask_stream("hi", model="llama", use_tools=False, user_id="alice")
        )
    )
    assert [e.type for e in events] == ["text", "error"]
    assert events[0].content == "first"
    assert events[1].content == loop._friendly_error(eg.EgressBlockedError())
    assert "egress guard" in events[1].content
    assert cb.get_breaker("llm:alice:llama", max_failures=1).state == "closed"
