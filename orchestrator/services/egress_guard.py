# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Opt-in in-process egress allowlist (LUM-553).

Defense-in-depth behind ADR 147 routing-policy enforcement. When enabled,
wraps LLM adapter calls with ``tethered.scope()`` so outbound sockets must
match a dynamically derived allowlist. Bypassable — routing policy remains
the hard guarantee.
"""

from __future__ import annotations

import contextlib
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

# Process-wide ceiling (LUM-571). Identity token required by ``tethered`` locked
# mode — must be a non-interned object (``str``/``int``/``bool`` literals can share
# identity and defeat the lock). Casual-mistake protection only, not attacker
# protection (any in-process Python can import it — honest in-process posture).
_CEILING_LOCK_TOKEN: object = object()
_ceiling_active: bool = False


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
    """Whether the process-wide egress ceiling is enabled (LUM-571).

    Independent of ``EGRESS_GUARD`` (the LLM-wrap flag): the ceiling is a
    whole-process ``tethered.activate(locked=True)`` cap installed at boot, not a
    per-call ``scope()``.
    """
    return features.is_enabled("EGRESS_GUARD_CEILING")


def activate_ceiling() -> None:
    """Install the process-wide egress ceiling (LUM-571) — call once at lifespan
    startup **after** store health checks.

    ``tethered.activate()`` and every ``scope()`` compose by **intersection**, so
    the ceiling allowlist (``build_allowlist(for_ceiling=True)``) must be a
    **superset** of every legitimate scope + every host any subsystem dials. The
    policy is **locked** (``deactivate()`` refused without the module token) and
    **baked at boot** — any allowlist-affecting change (privacy toggle, new
    mailbox/model, connector host) needs a **Core restart**.

    **Honest posture (binding, mirrors ADR-153):** an in-process PEP-578 audit
    hook — bypassable by C extensions / ``ctypes`` / raw syscalls / non-Python
    child processes (``external_subprocess_policy="warn"``) / a fully-compromised
    process. **Not** an OS-level firewall; never "impossible egress". On a
    local-only instance it excludes cloud hosts, blocking cloud egress
    process-wide **at the Python socket layer** (same bypassability caveats).

    **Fail-closed:** if the flag is on but ``tethered`` cannot be imported, raise
    ``RuntimeError`` (refuse boot) — an operator who enabled the ceiling asked for
    the guarantee; do not silently run without it.

    **``fail_closed=True`` (LUM-637):** if ``tethered``'s own policy check raises
    (a ``tethered`` bug), the connection is blocked rather than allowed. That
    branch wraps only ``AllowPolicy.is_allowed()``, which is total on every input
    the audit hook passes it: no DNS failure, malformed host or IPv6 form reaches
    it (measured under LUM-637, re-measured on ``tethered==1.1.1`` under LUM-903).
    Failing open there would silently disable the ceiling after a ``tethered``
    regression. The escape hatch is the flag itself: turn the ceiling off and
    restart. The per-call ``scope()`` doors keep the library default (fail open).

    **Event loop (LUM-903):** ``tethered>=1.0`` refuses ``activate()`` with
    ``RuntimeError`` when ``uvloop`` is already imported, because a uvloop event
    loop does its networking in libuv and raises no audit event (``0.5.x``
    accepted it and silently missed that traffic). Every Core launcher therefore
    runs uvicorn with ``--loop asyncio`` / ``loop="asyncio"``; a launcher that
    does not makes this call refuse boot, which is the fail-closed outcome.
    """
    global _ceiling_active
    if not egress_ceiling_enabled() or _ceiling_active:
        return
    try:
        import tethered
    except Exception as exc:  # tethered absent/broken — refuse boot (fail-closed)
        raise RuntimeError(
            "LUMOGIS_FF_EGRESS_GUARD_CEILING is enabled but `tethered` could not be "
            "imported; refusing to boot without the requested egress ceiling."
        ) from exc
    allow = sorted(build_allowlist(user_id=None, for_ceiling=True))
    tethered.activate(
        allow=allow,
        allow_localhost=True,
        locked=True,
        lock_token=_CEILING_LOCK_TOKEN,
        fail_closed=True,
        external_subprocess_policy="warn",
    )
    _ceiling_active = True
    # Count only — never the host list (tethered itself places it in
    # _TETHERED_CHILD_POLICY; that is the env-boundary reality, but our logs stay
    # count-only). Name the uncovered egress categories so "activated" doesn't mislead.
    _log.warning(
        "egress.ceiling.activated — process-wide egress ceiling active (%d hosts). "
        "Bypassable (in-process PEP-578 hook; not an OS firewall); does NOT cover "
        "external/non-Python subprocesses, web-push, or web-search/feeds. Allowlist "
        "baked at boot — restart Core to apply any change.",
        len(allow),
        extra={"event": "egress.ceiling.activated", "host_count": len(allow)},
    )


def imap_egress_enabled() -> bool:
    """Layer-1 IMAP egress guard toggle (LUM-625).

    **Independent** of ``EGRESS_GUARD`` (the LLM-wrap flag): enabling IMAP
    containment does not require opting into the LLM egress guard, and vice
    versa.
    """
    return features.is_enabled("IMAP_EGRESS_GUARD")


class ImapEgressProxyConfigError(ValueError):
    """``LUMOGIS_IMAP_EGRESS_PROXY`` is **set but unparseable**.

    Distinct from *unset*: an operator who sets this variable is asking for
    proxy containment, so a malformed value must **fail closed** (Core refuses
    to connect) rather than silently fall back to a direct connection that
    succeeds on a dual-homed Core and quietly removes the Layer-2 wall.
    """


def imap_egress_proxy() -> tuple[str, int] | None:
    """Parse ``LUMOGIS_IMAP_EGRESS_PROXY`` (``host:port``) — Layer 2 / Pass 3.

    When set, Core routes IMAP(S) egress through this HTTP ``CONNECT`` proxy (the
    LUM-618 Squid), so Core connects to the *proxy* and the proxy resolves +
    allowlists the mail host by SNI.

    Returns ``None`` when **unset**. Raises :class:`ImapEgressProxyConfigError`
    when **set but invalid** (fail-closed — a typo must not silently drop
    containment). Accepts bracketed IPv6 (``[::1]:3128``).
    """
    raw = os.environ.get("LUMOGIS_IMAP_EGRESS_PROXY", "").strip()
    if not raw:
        return None
    host, sep, port_s = raw.rpartition(":")
    host = host.strip()
    port_s = port_s.strip()
    if host.startswith("[") and host.endswith("]"):  # bracketed IPv6 literal
        host = host[1:-1].strip()
    if not sep or not host:
        raise ImapEgressProxyConfigError(f"{raw!r}: expected host:port")
    # A leftover ':' or whitespace in the host means a bad split (e.g. an
    # unbracketed IPv6, or interior spaces) — reject rather than mis-resolve.
    if any(c.isspace() for c in host) or (":" in host and not _looks_like_ipv6(host)):
        raise ImapEgressProxyConfigError(f"{raw!r}: malformed host {host!r}")
    try:
        port = int(port_s)
    except ValueError as exc:
        raise ImapEgressProxyConfigError(f"{raw!r}: bad port {port_s!r}") from exc
    if not (1 <= port <= 65535):
        raise ImapEgressProxyConfigError(f"{raw!r}: port {port} out of range")
    return (host, port)


def _looks_like_ipv6(host: str) -> bool:
    import ipaddress

    try:
        ipaddress.IPv6Address(host)
        return True
    except ValueError:
        return False


def imap_egress_target_host(conn) -> str:
    """The host Core actually opens a socket to for this connection.

    Must mirror ``imap_transport.connect``'s routing decision exactly, or the
    Layer-1 ``tethered`` scope allows a host the transport never dials and
    self-blocks the real connection (LUM-638). Core routes through the proxy
    only when a proxy is configured, TLS is in use, **and** the host is
    proxy-eligible (expressible in the Layer-2 SNI allow file — standard IMAPS
    port 993 + normalisable host). A proxy-ineligible host (IDN / IPv6 /
    nonstandard port) takes the **direct** path, so its target is ``conn.host``.
    """
    try:
        proxy = imap_egress_proxy()
    except ImapEgressProxyConfigError:
        # Misconfigured proxy → connect() will fail closed; the scope host is
        # moot. Allow conn.host so the scope itself doesn't mask the real error.
        return conn.host
    if proxy is not None and getattr(conn, "use_ssl", True) and _target_proxy_eligible(conn):
        return proxy[0]
    return conn.host


def _target_proxy_eligible(conn) -> bool:
    """Shared proxy-eligibility check for :func:`imap_egress_target_host`.

    Uses the same ``email_egress_acl.proxy_eligible`` decision as the real dialer
    (``imap_transport._proxy_eligible``) so the two never disagree. A ``conn``
    with no usable port is not proxy-eligible (falls back to the direct target).
    """
    port = getattr(conn, "port", None)
    if port is None:
        return False
    try:
        from services.email_egress_acl import proxy_eligible

        return proxy_eligible(conn.host, port)
    except Exception:
        # Eligibility unknowable → assume direct (conn.host); never mask the real
        # dial target behind the proxy host on an unexpected error.
        _log.warning("imap target-host proxy-eligibility check failed", exc_info=True)
        return False


@contextlib.contextmanager
def imap_egress_scope(conn, *, user_id: str | None = None):
    """Scope one IMAP connection so Core's only permitted egress host is the
    actual connect target (the proxy host when routing through the Pass-3 CONNECT
    proxy, otherwise ``conn.host``).

    Layer 1 of LUM-625 containment: reuses the ADR-153 ``tethered`` mechanism —
    verified (LUM-625 spike) to match on the **hostname** passed to
    ``getaddrinfo`` — around the ``imapclient.IMAPClient`` connect, so any code
    path that tries to reach a *different* host is refused in-process. Runs
    identically on Docker and native (Persona A/C parity).

    **Honest posture (mirrors ADR-153):** in-process ⇒ bypassable by a
    fully-compromised Core; defense-in-depth + a correctness gate, not an
    external wall. Because ``tethered`` keys on the hostname (not the resolved
    IP), DNS-rebinding to an internal IP is *not* caught here — the connect-time
    ``email_credentials.revalidate_imap_host`` re-check in the adapter closes
    that, independent of this scope.

    **Fail-safe-to-functional:** if the flag is off, or ``tethered`` cannot be
    imported, email keeps working (the ``_validate_imap_host`` SSRF guard still
    applies) — we never hard-block a first-party connector on a guard failure.
    """
    if not imap_egress_enabled():
        yield
        return
    try:
        import tethered
    except Exception:  # tethered absent — covered by test_guard_unavailable_fail_safe
        _log.error(
            "imap.egress.guard_unavailable — tethered import failed; "
            "IMAP egress falls back to the in-process SSRF guard only",
            extra={"event": "imap.egress.guard_unavailable"},
        )
        yield
        return
    try:
        with tethered.scope(
            allow=[imap_egress_target_host(conn)],
            allow_localhost=False,
            label="imap_egress_scope",
        ):
            yield
    except Exception as exc:
        # Translate tethered's block into our typed error (host/port preserved)
        # so callers — e.g. the write-back queue — can classify it.
        _reraise_if_egress_blocked(exc, user_id=user_id)
        raise


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


def _normalise_ceiling_host(host: str | None) -> str | None:
    """Lower-case + strip a trailing dot, matching the form ``getaddrinfo`` /
    ``imapclient`` actually dials (so a stored ``imap.mail.test.`` or mixed-case
    host can't silently mismatch its dialed form)."""
    if not host or not str(host).strip():
        return None
    h = str(host).strip().lower().rstrip(".")
    return h or None


def _ceiling_extra_hosts() -> frozenset[str]:
    """Ceiling-only superset additions (LUM-571) — hosts the whole-process ceiling
    must permit but the per-call LLM ``scope()`` need not.

    Each source is best-effort (swallow + log; one bad source must not sink the
    ceiling). See the LUM-571 plan for the P0 rationale: the IMAP scope allows the
    connect **target**, which is the proxy host under LUM-625 Layer 2 — so both the
    mail hosts (direct path + proxy-ineligible hosts) **and** the proxy host must be
    folded, unconditionally.
    """
    extra: set[str] = set()

    # P0 — IMAP connect targets: household mail hosts AND the configured proxy host.
    try:
        from services.email_egress_acl import household_imap_hosts

        for host, _port in household_imap_hosts():
            h = _normalise_ceiling_host(host)
            if h:
                extra.add(h)
    except Exception:
        _log.warning("ceiling: household IMAP host enumeration failed", exc_info=True)
    try:
        proxy = imap_egress_proxy()
        if proxy is not None:
            h = _normalise_ceiling_host(proxy[0])
            if h:
                extra.add(h)
    except Exception:
        # Malformed LUMOGIS_IMAP_EGRESS_PROXY (ImapEgressProxyConfigError) or other —
        # the direct path (mail hosts, folded above) still works; connect() itself
        # fails closed on a bad proxy config.
        _log.warning("ceiling: IMAP egress proxy host enumeration failed", exc_info=True)

    # P0 — SMTP submission targets (LUM-650): household send hosts, folded so the
    # scoped smtp_egress_scope connect (allow=[host]) is not self-blocked by the
    # process-wide ceiling. Direct path only (SMTP has no CONNECT proxy in v1).
    try:
        from services.smtp_transport import household_smtp_hosts

        for host, _port in household_smtp_hosts():
            h = _normalise_ceiling_host(host)
            if h:
                extra.add(h)
    except Exception:
        _log.warning("ceiling: household SMTP host enumeration failed", exc_info=True)

    # P0 — OAuth2 token-endpoint hosts (LUM-686): folded so an on-demand access-token
    # refresh (inside oauth_egress_scope) is not self-blocked by the ceiling. Only
    # for providers whose client_id is configured; DNS-free, best-effort.
    try:
        from services.email_oauth import declared_oauth_hosts

        for host in declared_oauth_hosts():
            h = _normalise_ceiling_host(host)
            if h:
                extra.add(h)
    except Exception:
        _log.warning("ceiling: oauth token-host enumeration failed", exc_info=True)

    # Infra hosts dialed at/after boot (env-derived, statically enumerable).
    # CALENDAR_CALDAV_URL covers only the single-user / AUTH_ENABLED=false calendar
    # env path — multi-user per-user CalDAV hosts are a named strict-mode casualty.
    for env_name in (
        "STACK_CONTROL_URL",
        "STT_SIDECAR_URL",
        "CALENDAR_CALDAV_URL",
    ):
        _add_env_host(extra, os.environ.get(env_name))
    # CAPABILITY_SERVICE_URLS is a comma/space-separated list (GRAPH_MODE=service
    # KG service etc.) — split and add each.
    raw_caps = os.environ.get("CAPABILITY_SERVICE_URLS", "") or ""
    for part in re.split(r"[\s,]+", raw_caps.strip()):
        _add_env_host(extra, part)

    return frozenset(_normalise_ceiling_host(h) or h for h in extra)


def build_allowlist(*, user_id: str | None, for_ceiling: bool) -> frozenset[str]:
    """Return normalized hostnames/IPs permitted for outbound sockets.

    ``for_ceiling=True`` (LUM-571) unions in :func:`_ceiling_extra_hosts` so the
    process-wide ceiling is a superset of every scope; ``for_ceiling=False``
    (the LLM/IMAP per-call scope path) is unchanged.
    """
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
            # LUM-766: harvest proxy_url here too, because the DIALER uses it.
            # `get_llm_provider` resolves `proxy = cfg.get("proxy_url")` with no
            # local/cloud distinction and hands the adapter `base_url=proxy or
            # base_url` (config.py:481,504) — so a model reached through a proxy
            # never dials its own base_url. Harvesting only base_url left the
            # allowlist disagreeing with the code that opens the socket, and the
            # guard self-blocked a mailbox-equivalent case: a legitimately-local
            # model behind a proxy. This is the same defect LUM-638 fixed on the
            # IMAP side (`imap_egress_target_host()` omitted `_proxy_eligible`);
            # the invariant it established — the scope target must agree with the
            # real dialer — applies to every transport, not just that one.
            #
            # NOT gated on `cloud_allowed`, deliberately, unlike the cloud branch
            # below. A local model is permitted regardless of privacy mode, so the
            # transport hop it is reached through must be too — gating it would
            # re-break the exact case this fixes on a privacy-locked instance,
            # which is where a corporate egress proxy is most likely to exist.
            # Residual, stated rather than hidden: an operator who sets a CLOUD
            # `proxy_url` on a model whose `base_url` marks it local (`is_local_model`
            # is a substring test for "ollama") gets that host allowlisted even
            # under local-only. That configuration is self-contradictory and
            # operator-authored; the place to refuse it is the privacy/model-enablement
            # path, not a silent omission here that surfaces as an unexplained
            # connection error. Pinned by test so the choice stays visible.
            proxy_host = _host_from_url(cfg.get("proxy_url"))
            if proxy_host:
                hosts.add(proxy_host)
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

    if for_ceiling:
        hosts.update(_ceiling_extra_hosts())

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
        """Stream from the inner adapter with every step of it scoped (LUM-909).

        The scope is entered around each step of the inner stream — creating
        it, every ``next()``, and ``close()`` — and is never open across our own
        ``yield``. ``tethered`` 1.x scopes are context-local (a ContextVar
        stack), and its guidance is explicit: inside a generator, wrap each I/O
        call in its own ``with tethered.scope(...)`` and do not ``yield`` inside
        one, because a scope open across a ``yield`` stays active in the
        consumer's context until the generator resumes. The old shape had two
        faults beyond the leak: Starlette steps a sync ``StreamingResponse``
        iterator through ``anyio.to_thread.run_sync``, each ``next()`` in a fresh
        copy of the context, so a scope entered by the first chunk was absent
        from every later one (those reads went unchecked), and the exit landed in
        a context that never entered it (tethered's "was exited in a context
        that did not enter it" warning). Every network read the adapter does
        happens inside one of these steps, so enforcement covers the whole
        stream in whatever context each step runs.
        """
        import tethered

        allow = list(build_allowlist(user_id=self._user_id, for_ceiling=False))

        def _scope():
            return tethered.scope(
                allow=allow,
                allow_localhost=False,
                label="EgressGuardingLLMProvider.chat_stream",
            )

        stream = None
        try:
            with _scope():
                try:
                    stream = iter(
                        self._inner.chat_stream(
                            messages, tools=tools, system=system, max_tokens=max_tokens
                        )
                    )
                except Exception as exc:
                    _reraise_if_egress_blocked(exc, user_id=self._user_id)
                    raise
            while True:
                with _scope():
                    try:
                        event = next(stream)
                    except StopIteration:
                        return
                    except Exception as exc:
                        _reraise_if_egress_blocked(exc, user_id=self._user_id)
                        raise
                yield event
        finally:
            # Consumers (loop.py) break on ``end`` without exhausting the
            # stream; close the inner generator here, scoped, so its cleanup
            # (closing the HTTP response) runs under the guard too.
            close = getattr(stream, "close", None)
            if close is not None:
                with _scope():
                    close()


def wrap_llm_provider_egress(inner, *, user_id: str | None):
    """Wrap an LLM adapter in scoped egress guarding when the flag is on."""
    if not egress_guard_enabled():
        return inner
    return EgressGuardingLLMProvider(inner, user_id=user_id)
