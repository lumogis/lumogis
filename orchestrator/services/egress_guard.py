# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Opt-in in-process egress allowlist (LUM-553).

Defense-in-depth behind ADR 147 routing-policy enforcement. When enabled,
wraps LLM adapter calls with ``tethered.scope()`` so outbound sockets must
match a dynamically derived allowlist. Bypassable — routing policy remains
the hard guarantee.
"""

from __future__ import annotations

import logging
import os
import re
import time
from urllib.parse import urlparse

import features
from models.privacy_mode import InstancePrivacyMode
from services.privacy_mode import effective_privacy_mode

import config

_log = logging.getLogger(__name__)

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_CLOUD_DEFAULT_HOSTS = {
    "anthropic": "api.anthropic.com",
    "openai": "api.openai.com",
}
_DYNAMIC_OLLAMA_TTL_S = 60.0

_dynamic_ollama_cache: frozenset[str] | None = None
_dynamic_ollama_cached_at: float = 0.0


class EgressBlockedError(RuntimeError):
    """Raised when tethered blocks an outbound socket during a scoped LLM call."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        message: str = "Outbound connection blocked by egress guard.",
    ) -> None:
        self.host = host
        self.port = port
        super().__init__(message)


def egress_guard_enabled() -> bool:
    return features.is_enabled("EGRESS_GUARD")


def egress_ceiling_enabled() -> bool:
    """Reserved for process-wide ceiling follow-up — always False in v1."""
    return False


def _host_from_url(url: str | None) -> str | None:
    if not url or not str(url).strip():
        return None
    parsed = urlparse(str(url).strip())
    host = (parsed.hostname or "").strip().lower()
    return host or None


def _add_env_host(hosts: set[str], value: str | None) -> None:
    if not value or not str(value).strip():
        return
    raw = str(value).strip()
    from_url = _host_from_url(raw)
    if from_url:
        hosts.add(from_url)
    else:
        hosts.add(raw.lower())


def _outbound_private_host_allowlist() -> frozenset[str]:
    raw = os.environ.get("LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST", "") or ""
    parts = re.split(r"[\s,]+", raw.strip())
    return frozenset(p.lower() for p in parts if p)


def _cloud_policy_allows(user_id: str | None) -> bool:
    return effective_privacy_mode(user_id).effective == InstancePrivacyMode.ALLOW_CLOUD


def _dynamic_ollama_hosts() -> frozenset[str]:
    global _dynamic_ollama_cache, _dynamic_ollama_cached_at

    now = time.monotonic()
    if (
        _dynamic_ollama_cache is not None
        and now - _dynamic_ollama_cached_at < _DYNAMIC_OLLAMA_TTL_S
    ):
        return _dynamic_ollama_cache

    hosts: set[str] = set()
    try:
        for cfg in config._dynamic_ollama_models().values():
            host = _host_from_url(cfg.get("base_url"))
            if host:
                hosts.add(host)
    except Exception:
        _log.warning("dynamic Ollama host discovery failed", exc_info=True)

    _dynamic_ollama_cache = frozenset(hosts)
    _dynamic_ollama_cached_at = now
    return _dynamic_ollama_cache


def build_allowlist(*, user_id: str | None, for_ceiling: bool) -> frozenset[str]:
    """Return normalized hostnames/IPs permitted for outbound sockets."""
    del for_ceiling  # reserved for ceiling follow-up — v1 always scoped-only

    hosts: set[str] = set(_LOOPBACK_HOSTS)

    _add_env_host(hosts, os.environ.get("POSTGRES_HOST", "postgres"))
    _add_env_host(hosts, os.environ.get("QDRANT_URL", "http://qdrant:6333"))
    _add_env_host(hosts, os.environ.get("FALKORDB_URL", "redis://falkordb:6379"))
    _add_env_host(hosts, os.environ.get("NTFY_URL"))
    _add_env_host(hosts, os.environ.get("OLLAMA_URL", "http://ollama:11434"))

    hosts.update(_outbound_private_host_allowlist())
    hosts.update(_dynamic_ollama_hosts())

    cloud_allowed = _cloud_policy_allows(user_id)
    for name, cfg in config.get_all_models_config().items():
        adapter = cfg.get("adapter", "")
        if config.is_local_model(name):
            host = _host_from_url(cfg.get("base_url"))
            if host:
                hosts.add(host)
            continue
        if not cloud_allowed:
            continue
        for url in (cfg.get("proxy_url"), cfg.get("base_url")):
            host = _host_from_url(url)
            if host:
                hosts.add(host)
        default_host = _CLOUD_DEFAULT_HOSTS.get(adapter)
        if default_host:
            hosts.add(default_host)

    return frozenset(hosts)


def _find_tethered_egress_blocked(exc: BaseException) -> BaseException | None:
    """Return tethered.EgressBlocked from exc or its __cause__ chain (SDK wrapping)."""
    import tethered

    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, tethered.EgressBlocked):
            return cur
        cur = cur.__cause__
    return None


def _translate_egress_block(exc: Exception, *, user_id: str | None) -> EgressBlockedError:
    host = getattr(exc, "host", None)
    port = getattr(exc, "port", None)
    _log.warning(
        "egress guard blocked outbound connection",
        extra={
            "event": "egress_blocked",
            "host": host,
            "port": port,
            "user_id": user_id,
        },
    )
    return EgressBlockedError(host=host, port=port)


def _reraise_if_egress_blocked(exc: BaseException, *, user_id: str | None) -> None:
    blocked = _find_tethered_egress_blocked(exc)
    if blocked is not None:
        raise _translate_egress_block(blocked, user_id=user_id) from exc


class EgressGuardingLLMProvider:
    """LLMProvider decorator that enters tethered.scope() around each call."""

    def __init__(self, inner, *, user_id: str | None) -> None:
        self._inner = inner
        self._user_id = user_id

    def chat(self, messages, tools=None, system=None, max_tokens=4096):
        import tethered

        allow = list(build_allowlist(user_id=self._user_id, for_ceiling=False))
        with tethered.scope(
            allow=allow,
            allow_localhost=False,
            label="EgressGuardingLLMProvider.chat",
        ):
            try:
                return self._inner.chat(messages, tools=tools, system=system, max_tokens=max_tokens)
            except Exception as exc:
                _reraise_if_egress_blocked(exc, user_id=self._user_id)
                raise

    def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
        import tethered

        allow = list(build_allowlist(user_id=self._user_id, for_ceiling=False))
        with tethered.scope(
            allow=allow,
            allow_localhost=False,
            label="EgressGuardingLLMProvider.chat_stream",
        ):
            try:
                yield from self._inner.chat_stream(
                    messages, tools=tools, system=system, max_tokens=max_tokens
                )
            except Exception as exc:
                _reraise_if_egress_blocked(exc, user_id=self._user_id)
                raise


def wrap_llm_provider_egress(inner, *, user_id: str | None):
    """Wrap an LLM adapter in scoped egress guarding when the flag is on."""
    if not egress_guard_enabled():
        return inner
    return EgressGuardingLLMProvider(inner, user_id=user_id)
