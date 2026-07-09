# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""PoC integration tests — real cloud adapter socket I/O under egress guard (LUM-570)."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Generator
from unittest.mock import patch

import main
import pytest
from auth import UserContext
from fastapi.testclient import TestClient
from openai import OpenAI

import config
from services import egress_guard as eg

try:
    import tethered  # noqa: F401
except ImportError:
    pytest.fail("tethered==0.5.1 required — install orchestrator/requirements-core.txt")

from tests.test_egress_guard import _PrivacyFakeStore

_OFF_ALLOWLIST_HOST = "93.184.216.34"
_ALICE = UserContext(user_id="alice", role="user", is_authenticated=True)

_POC_TOOL_DEF = {
    "name": "poc_noop",
    "description": "PoC",
    "parameters": {"type": "object", "properties": {}},
}

_NON_STREAM_OK_BODY = json.dumps(
    {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "poc-ok"},
                "finish_reason": "stop",
            }
        ],
    }
).encode()


def _passthrough_history(q, h, m, u, **kw):
    from models.context_injection import ContextInjectionResult

    return ContextInjectionResult(messages=h)


def _cleanup_llm_cache() -> None:
    for key in list(config._instances):
        if key.startswith("llm:"):
            del config._instances[key]
    eg._dynamic_ollama_cache = None
    eg._dynamic_ollama_cached_at = 0.0


def _narrow_allowlist_excluding_offlist(real_build):
    def _narrow(*, user_id=None, for_ceiling=False):
        hosts = set(real_build(user_id=user_id, for_ceiling=for_ceiling))
        hosts.discard(_OFF_ALLOWLIST_HOST)
        return frozenset(hosts)

    return _narrow


def _unwrap_openai_llm(provider):
    inner = provider
    while hasattr(inner, "_inner"):
        inner = inner._inner
    return inner


def _poc_models_entry(
    *,
    proxy_url: str,
    tools: bool,
) -> dict:
    return {
        "adapter": "openai",
        "model": "gpt-4o-mini",
        "tools": tools,
        "proxy_url": proxy_url,
        "api_key_env": "OPENAI_API_KEY",
    }


class _OpenAIHandler(BaseHTTPRequestHandler):
    """Plain-HTTP OpenAI-compatible handler for allowed-path PoC cases."""

    def log_message(self, format, *args):  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        payload = json.loads(body.decode("utf-8") or "{}")
        if payload.get("stream"):
            self._handle_stream()
        else:
            self._handle_non_stream()

    def _handle_non_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_NON_STREAM_OK_BODY)

    def _handle_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "delta": {"content": "poc"}, "finish_reason": None}],
        }
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


class _OpenAIToolRound1Handler(_OpenAIHandler):
    """Streaming round 1 — finish with tool_calls so the session loop reconnects."""

    def _handle_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        tool_chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_poc",
                                "type": "function",
                                "function": {"name": "poc_noop", "arguments": "{}"},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        }
        self.wfile.write(f"data: {json.dumps(tool_chunk)}\n\n".encode())
        done_chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        }
        self.wfile.write(f"data: {json.dumps(done_chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


class _MalformedJSONHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


@contextmanager
def _recording_audit_hook() -> Generator[list[tuple[str, tuple]], None, None]:
    events: list[tuple[str, tuple]] = []

    def _hook(event: str, args: tuple) -> None:
        if event in ("socket.connect", "socket.getaddrinfo"):
            events.append((event, args))

    sys.addaudithook(_hook)
    try:
        yield events
    finally:
        # Audit hooks cannot be removed; isolate by using a fresh list per test.
        pass


@pytest.fixture
def egress_poc_env(monkeypatch):
    store = _PrivacyFakeStore()
    store.app_settings["privacy_mode"] = "allow_cloud"
    monkeypatch.setitem(config._instances, "metadata_store", store)
    monkeypatch.setattr(config, "is_local_model", lambda name: False)
    monkeypatch.setattr("auth.auth_enabled", lambda: False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")

    from adapters.openai_llm import OpenAILLM

    def _fast_openai_init(self, model, base_url=None, api_key=None, context_budget=None):
        self._model = model
        self._is_ollama = "ollama" in (base_url or "").lower()
        self._context_budget = context_budget
        client_kwargs: dict = {"timeout": 5.0, "api_key": api_key or "not-needed"}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs, max_retries=0)

    monkeypatch.setattr(OpenAILLM, "__init__", _fast_openai_init)

    _cleanup_llm_cache()
    yield store
    _cleanup_llm_cache()


@pytest.fixture
def fake_openai_server(request):
    handler = getattr(request, "param", _OpenAIHandler)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}/v1"
    thread = server.serve_forever
    import threading

    worker = threading.Thread(target=thread, daemon=True)
    worker.start()
    try:
        yield host, port, base_url
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def poc_models_config(monkeypatch, request):
    blocked = getattr(request, "param", False)
    tools = getattr(request, "param_tools", False)

    def _apply(proxy_url: str, *, tools_flag: bool) -> None:
        entry = _poc_models_entry(proxy_url=proxy_url, tools=tools_flag)
        models = {"chatgpt": entry}

        monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))
        monkeypatch.setattr(config, "get_model_config", lambda name: dict(models[name]))

    if blocked:
        _apply(f"http://{_OFF_ALLOWLIST_HOST}/v1", tools_flag=tools)
        monkeypatch.setattr(
            eg,
            "build_allowlist",
            _narrow_allowlist_excluding_offlist(eg.build_allowlist),
        )
        yield
        return

    # Allowed path uses fake_openai_server via indirect parametrization in tests.
    yield _apply


@pytest.fixture
def chat_client_poc():
    with patch("routes.chat.get_user", return_value=_ALICE):
        with TestClient(main.app) as client:
            yield client


def _chat_post(client, *, stream: bool, tools: bool = False):
    body = {
        "model": "chatgpt",
        "stream": stream,
        "messages": [{"role": "user", "content": "hi"}],
    }
    if tools:
        body["tools"] = [_POC_TOOL_DEF]
    return client.post("/v1/chat/completions", json=body)


@patch("routes.chat.build_injected_context", side_effect=_passthrough_history)
def test_poc_non_stream_real_adapter_blocked_off_allowlist(
    _ctx, chat_client_poc, egress_poc_env, monkeypatch
):
    entry = _poc_models_entry(
        proxy_url=f"http://{_OFF_ALLOWLIST_HOST}/v1",
        tools=False,
    )
    models = {"chatgpt": entry}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))
    monkeypatch.setattr(config, "get_model_config", lambda name: dict(models[name]))
    monkeypatch.setattr(
        eg,
        "build_allowlist",
        _narrow_allowlist_excluding_offlist(eg.build_allowlist),
    )

    resp = _chat_post(chat_client_poc, stream=False)
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "egress_blocked"


@patch("routes.chat.build_injected_context", side_effect=_passthrough_history)
def test_poc_non_stream_real_adapter_allowed_on_allowlist(
    _ctx, chat_client_poc, egress_poc_env, fake_openai_server, monkeypatch
):
    _host, _port, base_url = fake_openai_server
    entry = _poc_models_entry(proxy_url=base_url, tools=False)
    models = {"chatgpt": entry}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))
    monkeypatch.setattr(config, "get_model_config", lambda name: dict(models[name]))

    resp = _chat_post(chat_client_poc, stream=False)
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "poc-ok"


@patch("routes.chat.build_injected_context", side_effect=_passthrough_history)
def test_poc_upstream_error_not_egress_blocked(_ctx, chat_client_poc, egress_poc_env, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MalformedJSONHandler)
    _host, port = server.server_address
    base_url = f"http://{_host}:{port}/v1"
    import threading

    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        entry = _poc_models_entry(proxy_url=base_url, tools=False)
        models = {"chatgpt": entry}
        monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))
        monkeypatch.setattr(config, "get_model_config", lambda name: dict(models[name]))

        resp = _chat_post(chat_client_poc, stream=False)
        assert resp.status_code >= 500
        text = resp.text
        assert "egress_blocked" not in text
        assert "egress guard" not in text.lower()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("fake_openai_server", [_OpenAIToolRound1Handler], indirect=True)
@patch("routes.chat.build_injected_context", side_effect=_passthrough_history)
@patch(
    "loop.prepare_llm_tools_for_request",
    return_value=([_POC_TOOL_DEF], None),
)
def test_poc_stream_tool_loop_second_connect_blocked(
    _prep_tools,
    _ctx,
    chat_client_poc,
    egress_poc_env,
    fake_openai_server,
    monkeypatch,
):
    _host, _port, base_url = fake_openai_server
    _cleanup_llm_cache()
    entry = _poc_models_entry(proxy_url=base_url, tools=True)
    models = {"chatgpt": entry}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))
    monkeypatch.setattr(config, "get_model_config", lambda name: dict(models[name]))

    repointed = {"done": False}

    def _dispatch_and_repoint(*_args, **_kwargs):
        if not repointed["done"]:
            repointed["done"] = True
            cache_key = "llm:alice:chatgpt"
            provider = config._instances.get(cache_key)
            if provider is not None:
                adapter = _unwrap_openai_llm(provider)
                try:
                    adapter._client.close()
                except Exception:
                    pass
                adapter._client = OpenAI(
                    base_url=f"http://{_OFF_ALLOWLIST_HOST}/v1",
                    api_key="sk-test-fake",
                    timeout=5.0,
                    max_retries=0,
                )
        return '{"ok": true}'

    with _recording_audit_hook() as connect_events:
        with patch("loop.dispatch_tool_under_cap", side_effect=_dispatch_and_repoint):
            resp = _chat_post(chat_client_poc, stream=True, tools=True)

    assert repointed["done"], "tool-loop round 2 did not run"
    assert resp.status_code == 200
    assert "egress guard" in resp.text.lower()
    socket_events = [e for e in connect_events if e[0] in ("socket.connect", "socket.getaddrinfo")]
    offlist_events = [e for e in socket_events if _OFF_ALLOWLIST_HOST in str(e[1])]
    assert len(socket_events) >= 2 or offlist_events, socket_events


@patch("routes.chat.build_injected_context", side_effect=_passthrough_history)
def test_poc_cache_invalidation_drops_llm_instance(
    _ctx, chat_client_poc, egress_poc_env, fake_openai_server, monkeypatch
):
    _host, _port, base_url = fake_openai_server
    entry = _poc_models_entry(proxy_url=base_url, tools=False)
    models = {"chatgpt": entry}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))
    monkeypatch.setattr(config, "get_model_config", lambda name: dict(models[name]))

    warm = _chat_post(chat_client_poc, stream=False)
    assert warm.status_code == 200
    assert "llm:alice:chatgpt" in config._instances

    config.invalidate_llm_cache_for_user("alice")
    assert "llm:alice:chatgpt" not in config._instances


@patch("routes.chat.build_injected_context", side_effect=_passthrough_history)
def test_poc_warm_pool_connect_recording(
    _ctx, chat_client_poc, egress_poc_env, fake_openai_server, monkeypatch
):
    _host, _port, base_url = fake_openai_server
    entry = _poc_models_entry(proxy_url=base_url, tools=False)
    models = {"chatgpt": entry}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))
    monkeypatch.setattr(config, "get_model_config", lambda name: dict(models[name]))

    with _recording_audit_hook() as connect_events:
        first = _chat_post(chat_client_poc, stream=False)
        second = _chat_post(chat_client_poc, stream=False)

    assert first.status_code == 200
    assert second.status_code == 200
    connects = [e for e in connect_events if e[0] == "socket.connect"]
    # Diagnostic: httpx may reuse the connection pool on the second call when
    # the allowlist is unchanged — documented limitation, not a bypass of block.
    assert len(connects) >= 1


@patch("routes.chat.build_injected_context", side_effect=_passthrough_history)
def test_poc_admin_invalidate_llm_cache_drops_per_user_keys(
    _ctx, chat_client_poc, egress_poc_env, fake_openai_server, monkeypatch
):
    _host, _port, base_url = fake_openai_server
    entry = _poc_models_entry(proxy_url=base_url, tools=False)
    models = {"chatgpt": entry}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))
    monkeypatch.setattr(config, "get_model_config", lambda name: dict(models[name]))

    warm = _chat_post(chat_client_poc, stream=False)
    assert warm.status_code == 200
    assert "llm:alice:chatgpt" in config._instances

    config.invalidate_llm_cache()
    assert "llm:alice:chatgpt" not in config._instances


@patch("routes.chat.build_injected_context", side_effect=_passthrough_history)
def test_poc_guard_disabled_allows_fake_server(
    _ctx, chat_client_poc, egress_poc_env, fake_openai_server, monkeypatch
):
    monkeypatch.delenv("LUMOGIS_FF_EGRESS_GUARD", raising=False)
    _host, _port, base_url = fake_openai_server
    entry = _poc_models_entry(proxy_url=base_url, tools=False)
    models = {"chatgpt": entry}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))
    monkeypatch.setattr(config, "get_model_config", lambda name: dict(models[name]))

    resp = _chat_post(chat_client_poc, stream=False)
    assert resp.status_code == 200


@patch("routes.chat.build_injected_context", side_effect=_passthrough_history)
def test_poc_privacy_local_only_blocks_before_adapter(
    _ctx, chat_client_poc, egress_poc_env, fake_openai_server, monkeypatch
):
    egress_poc_env.app_settings["privacy_mode"] = "local_only"
    _host, _port, base_url = fake_openai_server
    entry = _poc_models_entry(proxy_url=base_url, tools=False)
    models = {"chatgpt": entry}
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(models))
    monkeypatch.setattr(config, "get_model_config", lambda name: dict(models[name]))

    resp = _chat_post(chat_client_poc, stream=False)
    assert resp.status_code in (403, 503)
    body = resp.json()
    err = body.get("error") or body.get("detail", {})
    if isinstance(err, dict):
        code = err.get("code") or err.get("error")
        assert (
            code in ("privacy_mode_blocked", "egress_blocked", None)
            or "privacy" in str(body).lower()
        )
