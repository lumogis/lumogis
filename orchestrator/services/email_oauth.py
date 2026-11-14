# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Provider-agnostic OAuth2 token core for the email connector (LUM-686 / 674a).

The first, provider-agnostic half of OAuth2 email connect (parent LUM-674):
mint / refresh a provider access token on demand from a stored refresh token,
and build the SASL string for SMTP ``AUTH XOAUTH2``. IMAP uses
``imapclient.oauth2_login`` (which builds its own).

674a shipped the token-refresh + XOAUTH2 machinery + the token-endpoint
constants. 674b (LUM-687) adds the **Device Authorization Grant** (RFC 8628)
state machine at the bottom (``start_device_flow``/``poll_device_flow`` + the
in-process owner-scoped ``_FLOWS`` store) — Microsoft/Outlook only; the routes
persist the resulting ``xoauth2`` credential. No provider ``device_code`` or
minted ``refresh_token`` is ever returned to the browser.

Security invariants:
* The token endpoint is a **code constant** keyed by a **validated** ``provider``
  (``_PROVIDER_TOKEN``) — a crafted payload can never redirect the POST (SSRF).
* Tokens ride the existing Fernet-encrypted credential store; the refresh runs
  inside :func:`oauth_egress_scope` (per-call egress allow) with the token host
  folded into the LUM-571 ceiling via :func:`declared_oauth_hosts`.
* :class:`OAuthRefreshError` messages carry the status code + provider **only** —
  never the POST body, response text, or any token material (the message reaches
  a dead-letter row via ``PermanentActionError(str(exc))``). The one addition
  (LUM-761) is an httpx exception **class name** and a retry-in seconds figure in
  the negative-cache hit message.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import math
import random
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any
from typing import NamedTuple
from urllib.parse import urlsplit

from services import connector_credentials as ccs

_log = logging.getLogger(__name__)

# Token endpoints only. Code constants, NEVER read from the payload — the
# SSRF-closing invariant.
_PROVIDER_TOKEN: dict[str, str] = {
    "google": "https://oauth2.googleapis.com/token",
    "microsoft": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
}

# Device Authorization Grant (RFC 8628) endpoints — LUM-687 (674b). **microsoft
# only** in v1: Google's device flow can't grant the restricted
# ``https://mail.google.com/`` scope (documented), so Gmail OAuth is deferred to
# a future auth-code+loopback path. The ``google`` token endpoint above stays
# (harmless, unused by v1 device flow).
_PROVIDER_DEVICE_AUTH: dict[str, str] = {
    "microsoft": "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode",
}
_PROVIDER_SCOPES: dict[str, str] = {
    "microsoft": (
        "https://outlook.office.com/IMAP.AccessAsUser.All "
        "https://outlook.office.com/SMTP.Send offline_access"
    ),
}
_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

# Refresh a little before real expiry so an in-flight connect never races the edge.
_EXPIRY_SKEW_SECONDS = 60
# Default access-token lifetime when the provider omits ``expires_in``.
_DEFAULT_EXPIRES_IN = 3600

# Per-PHASE httpx timeouts (httpx.Timeout bounds each phase, not the call — LUM-834).
_REFRESH_CONNECT_TIMEOUT_S = 5.0
_REFRESH_WRITE_TIMEOUT_S = 5.0
_REFRESH_READ_TIMEOUT_S = 15.0  # today's value; not shortened without evidence
_REFRESH_POOL_TIMEOUT_S = 5.0
#: One attempt's NOMINAL worst case: connect + write + read (the pool phase is
#: immediate on a fresh client).
_REFRESH_ATTEMPT_WORST_CASE_S = (
    _REFRESH_CONNECT_TIMEOUT_S + _REFRESH_WRITE_TIMEOUT_S + _REFRESH_READ_TIMEOUT_S
)
#: Overall monotonic deadline for ADMITTING the retry. With these constants it never
#: refuses (a retry follows a < 3 s failure); it guards future edits of any one
#: constant. It is NOT a wall-clock ceiling and does not interrupt a running
#: attempt: httpx read/write timeouts bound each socket operation (a trickling
#: server can exceed them), ``getaddrinfo`` is not bounded by the connect timeout,
#: ``socket.create_connection`` applies the connect timeout per resolved address,
#: and behind ``HTTPS_PROXY`` the proxy's upstream connect runs inside the read
#: timeout. All of these exist today for every httpx caller (LUM-761 plan).
_REFRESH_DEADLINE_S = 30.0
#: A ConnectError faster than this is "never sent" and may be retried once. The
#: measured time includes ``oauth_egress_scope`` and ``httpx.Client`` construction
#: (milliseconds), not only the connect.
_FAST_CONNECT_FAILURE_S = 3.0
_RETRY_PAUSE_RANGE_S = (0.5, 1.0)
#: Negative cache (LUM-761): honour Retry-After by NOT CALLING, never by sleeping.
#: Applies to any failure without a Retry-After: 429/5xx without the header,
#: transport error, non-object 2xx. MSAL Python uses 5 s, MSAL.NET 60 s (5xx, public
#: client); 10 s is a judgement for the send path, which dead-letters on first
#: failure (ADR 201).
_BACKOFF_DEFAULT_TTL_S = 10.0
_BACKOFF_MIN_TTL_S = 5.0  # Microsoft: no retry of a 5xx sooner than 5 s
_BACKOFF_MAX_TTL_S = 600.0  # cap; a higher Retry-After is clamped
# Per phase, not per call (LUM-834). The device flow is out of LUM-761's scope.
_DEVICE_TIMEOUT_SECONDS = 15.0
_SLOW_DOWN_INCREMENT_SECONDS = 5  # RFC 8628 §3.5 minimum
_DEFAULT_DEVICE_EXPIRES_IN = 900

#: ADR 195's mailbox-health codes, reused verbatim as OAuthRefreshError.kind
#: values (LUM-757). NOT a parallel vocabulary — feed_monitor returns these
#: straight through to `sources.last_poll_error_code`, and the web copy in
#: `pollHealthMessage.ts` renders exactly these four.
_KINDS = frozenset({"credential", "network", "config", "egress_blocked"})
_MAX_FLOWS_PER_USER = 20  # evict-oldest at the cap (a user rarely has >1 in-flight)


# Test seams (LUM-761): monkeypatched by tests, never by production code.
def _now_monotonic() -> float:
    return time.monotonic()


def _now_wall() -> float:
    return time.time()


def _retry_pause() -> None:
    time.sleep(random.uniform(*_RETRY_PAUSE_RANGE_S))


def _refresh_timeout():
    import httpx

    return httpx.Timeout(
        connect=_REFRESH_CONNECT_TIMEOUT_S,
        read=_REFRESH_READ_TIMEOUT_S,
        write=_REFRESH_WRITE_TIMEOUT_S,
        pool=_REFRESH_POOL_TIMEOUT_S,
    )


class OAuthRefreshError(ccs.CredentialUnavailable):
    """A token refresh or its configuration failed.

    Subclasses :class:`connector_credentials.CredentialUnavailable` (a
    ``RuntimeError``) so the whole refresh/config/None-token family is one type;
    the SMTP action handler catches **this** type specifically (not the broad
    base) so app-password credential errors keep their existing surface.

    The message must carry only a status code + provider name (or an httpx
    exception class name and a retry-in seconds figure) — never the refresh POST
    body, the token-endpoint response text, or any token.

    ``kind`` is one of ADR 195's mailbox-health codes and is decided **here**, at
    the raise site, for the same reason :class:`ImapConnectError.kind` is: the
    token endpoint's response is in scope here and nowhere else, and the message
    deliberately cannot carry it (see above). ``feed_monitor`` reads it rather
    than guessing, and ``smtp_send`` picks its error code from it (LUM-757).

    Defaults to ``"credential"`` so a raise site that forgets degrades to the
    pre-LUM-757 behaviour rather than to something new, and an **unrecognised**
    value is coerced to the same — the classifier writes this string verbatim
    into ``sources.last_poll_error_code``, so a typo would otherwise reach the DB
    and render as the UI's fallback copy.
    """

    def __init__(self, message: str, *, kind: str = "credential") -> None:
        super().__init__(message)
        if kind not in _KINDS:
            _log.warning("oauth_refresh_error_unknown_kind kind=%r", kind)
            kind = "credential"
        self.kind = kind


def _kind_for_token_error(status: int, err: str | None) -> str:
    """Map a non-2xx token-endpoint response to an ADR 195 health code (LUM-757).

    Keys on **RFC 6749 §5.2 `error` values**, never on provider-specific strings:
    `invalid_grant` and `invalid_client` are standard, whereas `AADSTS50173` is
    Microsoft-only and would not survive a second provider (LUM-689).

    Status wins over body for 429/5xx: a provider that rate-limits while echoing
    a stale `error` must not send anyone to re-authenticate.
    """
    if status == 429 or status >= 500:
        return "network"
    if err == "invalid_grant":
        # Revoked, expired, password change, MFA reset, or 90-day inactivity —
        # all genuinely need the user to reconnect.
        return "credential"
    if err in ("invalid_client", "unauthorized_client"):
        # The *app* cannot authenticate: a wrong or unregistered client id. The
        # household member cannot fix this and must not be told to try.
        return "config"
    return "credential"


def _token_error_code(resp: Any) -> str | None:
    """The RFC 6749 `error` value, or None. Never raises, never returns a body.

    The status check runs *before* the response is parsed for the happy path, so
    this does its own guarded parse — and a failure to parse an error body must
    not mask the error it was trying to describe.
    """
    try:
        parsed = resp.json()
    except Exception:  # noqa: BLE001 - an unparseable error body is not an error
        return None
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("error")
    return raw if isinstance(raw, str) else None


def _url_host(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.lower().rstrip(".")


def _token_host(provider: str) -> str:
    return _url_host(_PROVIDER_TOKEN[provider])


def has_oauth_provider(provider: str | None) -> bool:
    """True when ``provider`` supports the device flow AND the operator's BYO
    ``client_id`` is configured — the gate for advertising/offering OAuth."""
    return bool(provider) and provider in _PROVIDER_DEVICE_AUTH and bool(_client_id(provider))


def _client_id(provider: str) -> str | None:
    """The operator's BYO OAuth client id for ``provider`` (installation-wide env),
    or ``None`` when unset. Reads through the ``config`` factory helper (env +
    factory pattern) rather than ``os.environ`` directly."""
    import config

    return config.oauth_client_id(provider)


@contextlib.contextmanager
def oauth_egress_scope(host: str, *, user_id: str | None = None):
    """Scope one OAuth token-refresh POST so the only permitted egress host is
    ``host`` (a per-provider **constant** token endpoint).

    A full mirror of :func:`services.smtp_transport.smtp_egress_scope`:
    fail-safe-to-functional — flag off / ``tethered`` absent ⇒ the refresh
    proceeds (the token endpoint is a fixed HTTPS constant). A tethered block is
    translated into :class:`~services.egress_guard.EgressBlockedError`.
    """
    from services.egress_guard import _reraise_if_egress_blocked
    from services.egress_guard import imap_egress_enabled

    if not imap_egress_enabled():
        yield
        return
    try:
        import tethered
    except Exception:  # tethered absent — fail-safe to functional
        _log.error(
            "oauth.egress.guard_unavailable — tethered import failed; "
            "OAuth token refresh falls back to the fixed-endpoint constant only",
            extra={"event": "oauth.egress.guard_unavailable"},
        )
        yield
        return
    try:
        with tethered.scope(allow=[host], allow_localhost=False, label="oauth_egress_scope"):
            yield
    except Exception as exc:
        _reraise_if_egress_blocked(exc, user_id=user_id)
        raise


def declared_oauth_hosts() -> list[str]:
    """Token-endpoint hosts for providers whose ``client_id`` is configured —
    folded into the LUM-571 egress ceiling so a refresh isn't self-blocked.

    Best-effort + DNS-free (mirrors ``declared_imap_hosts``): a provider with no
    ``client_id`` is skipped. (674b adds device-auth hosts.)
    """
    hosts: set[str] = set()
    for provider, url in {**_PROVIDER_TOKEN, **_PROVIDER_DEVICE_AUTH}.items():
        try:
            if _client_id(provider):
                host = _url_host(url)
                if host:
                    hosts.add(host)
        except Exception:  # noqa: BLE001 - one bad provider must not sink the ceiling
            _log.warning("oauth.ceiling.enumerate_skip provider=%s", provider)
    return sorted(hosts)


def build_xoauth2_auth_string(username: str, access_token: str) -> str:
    """The SASL XOAUTH2 initial-response string for SMTP ``AUTH XOAUTH2``:
    ``base64("user=" + user + "\\x01auth=Bearer " + token + "\\x01\\x01")``.
    IMAP uses ``imapclient.oauth2_login`` which builds its own."""
    raw = f"user={username}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


# Per-mailbox refresh serialization — concurrent ``get_access_token`` calls (e.g.
# a poll tick and an SMTP send both hitting an expired access token) must not
# POST the same refresh_token twice. The lock prevents duplicate POSTs and a lost
# rotation write (two refreshes, two persisted rotations, last writer wins).
# Microsoft does *not* revoke old refresh tokens when they are redeemed
# (https://learn.microsoft.com/entra/identity-platform/refresh-tokens), so a double
# refresh is wasteful there, not fatal. Providers with RFC 9700 reuse detection
# would differ, which is why only a never-sent POST is retried in place (LUM-761).
_REFRESH_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}
_REFRESH_LOCKS_GUARD = threading.Lock()


def _refresh_lock(user_id: str, connector: str, instance: str) -> threading.Lock:
    key = (user_id, connector, instance)
    with _REFRESH_LOCKS_GUARD:
        lock = _REFRESH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _REFRESH_LOCKS[key] = lock
        return lock


# ---------------------------------------------------------------------------
# Refresh negative cache (LUM-761) — process-local, like the lock above (both rely
# on the single-process invariant, LUM-832). A `network`-kind failure records a
# "do not call before" time per mailbox; until it passes, callers get the same
# ``OAuthRefreshError(kind="network")`` without a POST. ``Retry-After`` is honoured
# by NOT CALLING, never by sleeping.
# ---------------------------------------------------------------------------

_BackoffKey = tuple[str, str, str, str]  # (user_id, connector, instance, rt_digest)


class _BackoffEntry(NamedTuple):
    not_before: float  # monotonic
    last_failure: str  # "503", "429", "ConnectError", "non-JSON" — never a body/token


_REFRESH_BACKOFF: dict[_BackoffKey, _BackoffEntry] = {}
_REFRESH_BACKOFF_GUARD = threading.Lock()


def _backoff_key(user_id: str, connector: str, instance: str, refresh_token: str) -> _BackoffKey:
    # The digest only makes a reconnect (a new refresh token) bypass an old entry.
    # It lives in memory and is NEVER logged or put in a message.
    digest = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:16]
    return (user_id, connector, instance, digest)


def _prune_expired_locked(now: float) -> None:
    for k in [k for k, e in _REFRESH_BACKOFF.items() if e.not_before <= now]:
        del _REFRESH_BACKOFF[k]


def _backoff_entry(key: _BackoffKey) -> _BackoffEntry | None:
    with _REFRESH_BACKOFF_GUARD:
        _prune_expired_locked(_now_monotonic())
        return _REFRESH_BACKOFF.get(key)


def _record_backoff(
    key: _BackoffKey,
    ttl_s: float,
    *,
    provider: str,
    last_failure: str,
    retry_after_present: bool,
) -> None:
    """Hold this mailbox for ``ttl_s``. At most one entry per mailbox (IMAP and SMTP
    share it: ``get_access_token`` has one production caller, ``load_connection``).

    Transport-class entries are written by :func:`_post_refresh` (it alone sees the
    exception); 429/5xx and malformed-2xx entries by :func:`get_access_token` (it
    alone sees the response).
    """
    now = _now_monotonic()
    with _REFRESH_BACKOFF_GUARD:
        for k in [k for k in _REFRESH_BACKOFF if k[:3] == key[:3]]:
            del _REFRESH_BACKOFF[k]
        _prune_expired_locked(now)
        _REFRESH_BACKOFF[key] = _BackoffEntry(now + ttl_s, last_failure)
    _log.warning(
        "oauth_refresh_backoff provider=%s connector=%s instance=%s ttl_s=%.0f "
        "last_failure=%s retry_after=%s",
        provider,
        key[1],
        key[2],
        ttl_s,
        last_failure,
        "yes" if retry_after_present else "no",
    )


def _clear_backoff(user_id: str, connector: str, instance: str) -> None:
    prefix = (user_id, connector, instance)
    with _REFRESH_BACKOFF_GUARD:
        for k in [k for k in _REFRESH_BACKOFF if k[:3] == prefix]:
            del _REFRESH_BACKOFF[k]


def _reset_refresh_backoff() -> None:
    """Test helper: forget every entry (mirrors ``circuit_breaker.reset_all``)."""
    with _REFRESH_BACKOFF_GUARD:
        _REFRESH_BACKOFF.clear()


def _raise_if_backing_off(key: _BackoffKey, provider: str) -> None:
    entry = _backoff_entry(key)
    if entry is None:
        return
    remaining = max(1, math.ceil(entry.not_before - _now_monotonic()))
    _log.debug("oauth_refresh_backoff_hit provider=%s", provider)
    # No "deferred": on the send path the proposal is dead-lettered (ADR 201) and
    # this text reaches the dead-letter row and the composer.
    raise OAuthRefreshError(
        f"token refresh unavailable for {provider} "
        f"(last failure: {entry.last_failure}; retry in ~{remaining} s)",
        kind="network",
    )


def _http_date_ts(value: str) -> float | None:
    """An HTTP-date as a POSIX timestamp, or None. Never raises.

    All three HTTP-date forms are GMT by definition, but ``parsedate_to_datetime``
    returns a NAIVE datetime for asctime and for a ``-0000`` zone — treat those as UTC.
    """
    try:
        dt = parsedate_to_datetime(value.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:  # noqa: BLE001 - garbage in, None out
        return None


def _response_wall_time(resp: Any) -> float:
    """The reference instant for an HTTP-date ``Retry-After``: the response's own
    ``Date`` header when it parses (so a household host with a skewed clock still
    waits the interval the server meant), else the local wall clock."""
    try:
        raw = resp.headers.get("Date") or ""
    except Exception:  # noqa: BLE001
        raw = ""
    ts = _http_date_ts(raw)
    return ts if ts is not None else _now_wall()


def _parse_retry_after(value: str | None, *, reference_wall: float) -> float | None:
    """RFC 9110 §10.2.3: delay-seconds (1*DIGIT) or HTTP-date, in seconds from now.
    None on absent or garbage. Never raises.

    Repeated ``Retry-After`` headers reach here joined as ``"120, 120"`` — garbage
    to this parser (splitting on the comma is unsafe: an HTTP-date contains one),
    so they get the default TTL.
    """
    try:
        if value is None:
            return None
        v = value.strip()
        if not v:
            return None
        if v.isascii() and v.isdigit():
            v = v.lstrip("0") or "0"
            if len(v) > 9:  # never int() a huge digit string (3.11+ digit limit)
                return 1e9
            return float(int(v))
        ts = _http_date_ts(v)
        if ts is None:
            return None
        return max(0.0, ts - reference_wall)
    except Exception:  # noqa: BLE001
        return None


def _backoff_ttl(retry_after: float | None) -> float:
    if retry_after is None:
        return _BACKOFF_DEFAULT_TTL_S
    return min(max(retry_after, _BACKOFF_MIN_TTL_S), _BACKOFF_MAX_TTL_S)


def _post_refresh(
    token_url: str,
    data: dict,
    host: str,
    user_id: str,
    provider: str,
    key: _BackoffKey,
) -> Any:
    """POST the refresh, with at most ONE in-place retry, and return the response.

    The retry is used only when the first POST provably never reached the server: a
    fast ``httpx.ConnectError``. Anything ambiguous (a read timeout, a protocol
    error) is NOT retried — a rolling-refresh provider may already have redeemed the
    token. Transport-class failures are negative-cached here; 429/5xx and malformed
    2xx answers are cached by :func:`get_access_token`, which alone sees the response.
    """
    import httpx

    # Local import for the same reason `oauth_egress_scope` does it — a module-level
    # `services.egress_guard` import here is circular
    # (egress_guard -> config -> connector_credentials -> ...).
    #
    # It sits *before* the try deliberately: inside, an ImportError would
    # leave `_EgressBlockedError` unbound and the except clause would raise
    # NameError while handling another exception. Out here an unimportable
    # egress_guard surfaces as a plain ImportError classified `credential`
    # rather than the `network` it would previously have become — a shift
    # this change makes and does not fix. It is unreachable in practice:
    # `oauth_egress_scope` imports the same module a few lines later, so a
    # broken egress_guard breaks the call regardless.
    from services.egress_guard import EgressBlockedError as _EgressBlockedError

    deadline = _now_monotonic() + _REFRESH_DEADLINE_S
    attempt = 0
    while True:
        attempt += 1
        started = _now_monotonic()
        try:
            # The scope is entered PER ATTEMPT and is load-bearing: it translates a
            # tethered block to `_EgressBlockedError` before the ConnectError branch
            # below can mistake it for an outage. Do not hoist it out of the loop.
            with oauth_egress_scope(host, user_id=user_id):
                with httpx.Client(timeout=_refresh_timeout()) as client:
                    return client.post(token_url, data=data)
        except OAuthRefreshError:
            raise
        except _EgressBlockedError as exc:
            # MUST precede the transport handlers below. `oauth_egress_scope` turns a
            # tethered block into this type, and folding it into "network" would
            # tell a household member the problem "usually clears up on its own"
            # when it never does without an operator editing the allowlist and
            # restarting Core. `feed_monitor._classify_connect_failure` already
            # treats this type as `egress_blocked` for IMAP; this keeps the two
            # surfaces telling the same story (ADR 195, LUM-757). Never cached.
            raise OAuthRefreshError(
                f"token refresh blocked by egress policy for {provider}",
                kind="egress_blocked",
            ) from exc
        except httpx.ConnectError as exc:  # never sent (precedes HTTPError: subclass)
            fast = (_now_monotonic() - started) < _FAST_CONNECT_FAILURE_S
            # Checked BEFORE the pause, and includes the longest pause, so a refused
            # retry never sleeps while holding the mailbox lock.
            room = deadline - _now_monotonic()
            if (
                attempt == 1
                and fast
                and room >= _RETRY_PAUSE_RANGE_S[1] + _REFRESH_ATTEMPT_WORST_CASE_S
            ):
                _log.info("oauth_refresh_retry_connect_error provider=%s", provider)
                _retry_pause()
                continue
            _record_backoff(
                key,
                _BACKOFF_DEFAULT_TTL_S,
                provider=provider,
                last_failure=type(exc).__name__,
                retry_after_present=False,
            )
            # Never embed the exception's detail (may echo the request) — code+provider only.
            raise OAuthRefreshError(
                f"token refresh network error for {provider}", kind="network"
            ) from exc
        except httpx.HTTPError as exc:
            # Ambiguous transport failure (ReadTimeout, ConnectTimeout, ProxyError,
            # RemoteProtocolError…): the request may have been received. Not retried.
            _record_backoff(
                key,
                _BACKOFF_DEFAULT_TTL_S,
                provider=provider,
                last_failure=type(exc).__name__,
                retry_after_present=False,
            )
            raise OAuthRefreshError(
                f"token refresh network error for {provider}", kind="network"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - not an httpx error: a bug, not an outage
            # Same kind as before this change, but NOT cached, and logged once with
            # the class name only: no message, no traceback — the POST body in the
            # frame holds the refresh token.
            _log.error(
                "oauth_refresh_unexpected_error provider=%s exc_type=%s",
                provider,
                type(exc).__name__,
            )
            raise OAuthRefreshError(
                f"token refresh network error for {provider}", kind="network"
            ) from exc


def get_access_token(user_id: str, *, connector: str, instance: str, payload: dict) -> str:
    """Return a valid access token for the stored xoauth2 ``payload``, refreshing
    on demand from its ``refresh_token``.

    ``payload`` is the **already-decrypted** credential dict the caller
    (``load_connection``) holds. A non-expired cached ``access_token`` is returned
    without a network call; else a ``grant_type=refresh_token`` POST to the
    provider's **constant** token endpoint (inside :func:`oauth_egress_scope`) mints
    a new one under a per-mailbox lock (concurrent callers double-check the store
    after waiting), which is persisted back (with the rotated ``refresh_token`` if
    the provider returns one) via ``put_payload(..., actor="system", ...)``.

    A ``network``-kind failure (429/5xx, transport error, malformed 2xx) is
    negative-cached per mailbox (LUM-761): until the time passes — ``Retry-After``
    clamped to 5–600 s, else 10 s — callers get the same ``OAuthRefreshError`` with
    no POST. A connection that fails fast, before anything is sent, is retried
    once in place. A valid cached access token is never blocked by the cache.

    Raises :class:`OAuthRefreshError` on unknown provider / missing refresh_token /
    missing client_id / non-2xx / missing access_token / network error — never a
    stale or blank token, never persisting on failure.
    """
    provider = payload.get("provider")
    if provider not in _PROVIDER_TOKEN:
        # Unknown/absent provider — the payload can never redirect the POST.
        raise OAuthRefreshError(
            f"unknown oauth provider for token refresh: {provider!r}", kind="config"
        )

    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise OAuthRefreshError(
            f"xoauth2 payload missing refresh_token for {provider}", kind="credential"
        )

    now = time.time()
    cached = payload.get("access_token")
    expiry = payload.get("expiry")
    if (
        isinstance(cached, str)
        and cached
        and isinstance(expiry, (int, float))
        and expiry - _EXPIRY_SKEW_SECONDS > now
    ):
        return cached

    with _refresh_lock(user_id, connector, instance):
        # Double-checked: another thread may have refreshed while we waited.
        fresh = ccs.get_payload(user_id, connector, instance=instance)
        if not isinstance(fresh, dict):
            # Fall back to the caller's read (``load_connection`` just decrypted it).
            # Keeps the refresh path working when the store is monkeypatched in tests;
            # a concurrent delete between the outer read and here is already exceptional.
            fresh = payload
        if not isinstance(fresh, dict):
            raise OAuthRefreshError(f"xoauth2 payload missing for {provider!r}", kind="credential")

        now = time.time()
        cached = fresh.get("access_token")
        expiry = fresh.get("expiry")
        if (
            isinstance(cached, str)
            and cached
            and isinstance(expiry, (int, float))
            and expiry - _EXPIRY_SKEW_SECONDS > now
        ):
            return cached

        provider = fresh.get("provider")
        if provider not in _PROVIDER_TOKEN:
            raise OAuthRefreshError(
                f"unknown oauth provider for token refresh: {provider!r}", kind="config"
            )

        refresh_token = fresh.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise OAuthRefreshError(
                f"xoauth2 payload missing refresh_token for {provider}", kind="credential"
            )

        client_id = _client_id(provider)
        if not client_id:
            raise OAuthRefreshError(f"no client_id configured for {provider}", kind="config")

        # A waiter whose lock holder just failed fails fast here, with no POST: an
        # entry is written by the holder just before it releases the lock. A rotated
        # token in `fresh` gives a new key, so there is no false hit. Placed after
        # the config checks so a removed client_id still reports `config`.
        key = _backoff_key(user_id, connector, instance, refresh_token)
        _raise_if_backing_off(key, provider)

        token_url = _PROVIDER_TOKEN[provider]
        host = _token_host(provider)
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }

        resp = _post_refresh(token_url, data, host, user_id, provider, key)

        if resp.status_code // 100 != 2:
            kind = _kind_for_token_error(resp.status_code, _token_error_code(resp))
            if kind == "network":
                ra = _parse_retry_after(
                    resp.headers.get("Retry-After"), reference_wall=_response_wall_time(resp)
                )
                _record_backoff(
                    key,
                    _backoff_ttl(ra),
                    provider=provider,
                    last_failure=str(resp.status_code),
                    retry_after_present=ra is not None,
                )
            # Status code + provider only — NEVER the response body (may carry detail).
            raise OAuthRefreshError(
                f"token refresh failed ({resp.status_code}) for {provider}",
                kind=kind,
            )

        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            # Reached only on a 2xx whose body will not parse — the canonical
            # cause is a captive portal answering 200 with an HTML login page,
            # which is a network condition. A provider contract change would
            # also land here; `network` is the recoverable reading. A captive
            # portal is not the token server answering, so any Retry-After on it
            # is ignored and the default TTL applies.
            _record_backoff(
                key,
                _BACKOFF_DEFAULT_TTL_S,
                provider=provider,
                last_failure="non-JSON",
                retry_after_present=False,
            )
            raise OAuthRefreshError(
                f"token refresh returned non-JSON for {provider}",
                kind="network",
            ) from exc

        if not isinstance(body, dict):
            # `[]`, `"x"`, `null`: would otherwise escape as a raw AttributeError.
            _record_backoff(
                key,
                _BACKOFF_DEFAULT_TTL_S,
                provider=provider,
                last_failure="non-JSON",
                retry_after_present=False,
            )
            raise OAuthRefreshError(
                f"token refresh returned non-JSON for {provider}", kind="network"
            )

        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthRefreshError(
                f"token refresh returned no access_token for {provider}", kind="config"
            )

        expires_in = body.get("expires_in")
        if not isinstance(expires_in, (int, float)) or expires_in <= 0:
            expires_in = _DEFAULT_EXPIRES_IN

        # Build a COPY — never mutate the caller's dict (load_connection still holds it).
        updated = dict(fresh)
        updated["access_token"] = access_token
        updated["expiry"] = now + float(expires_in)
        rotated = body.get("refresh_token")
        if isinstance(rotated, str) and rotated:
            updated["refresh_token"] = rotated

        ccs.put_payload(user_id, connector, updated, actor="system", instance=instance)
        # Only after the rotation is persisted: if put_payload raised, the entry
        # survives and the next caller does not POST straight away.
        _clear_backoff(user_id, connector, instance)
        return access_token


# ---------------------------------------------------------------------------
# Device Authorization Grant (RFC 8628) — LUM-687 (674b)
#
# The browser polls Core; Core is the RFC 8628 client (the browser can't reach
# the provider through the egress guard). In-flight state lives in a per-user,
# owner-scoped, in-process TTL store (``_FLOWS``) — the provider ``device_code``
# and the minted ``refresh_token`` NEVER reach the browser. Persistence is the
# ROUTE's job (keep this module DB/adapter-free); ``poll_device_flow`` hands the
# route the tokens on success and the route live-verifies + persists.
# ---------------------------------------------------------------------------


class OAuthDeviceError(RuntimeError):
    """A device-authorization start/poll failed (network / non-2xx / unknown
    provider error / no refresh_token). Message carries status + provider only —
    never a token or a device_code."""


@dataclass(frozen=True)
class DeviceFlow:
    """The browser-safe result of ``start_device_flow`` (no ``device_code``)."""

    flow_id: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    interval: int
    expires_in: int


@dataclass(frozen=True)
class DevicePoll:
    """The result of one ``poll_device_flow``. ``refresh_token``/``access_token``
    are populated ONLY on ``authorized`` and are handed to the route (never to
    the browser)."""

    status: str  # pending | authorized | expired | denied | not_found
    refresh_token: str | None = None
    access_token: str | None = None
    expiry: float | None = None


_FLOWS: dict[str, dict] = {}
_FLOWS_LOCK = threading.Lock()


def _prune_locked(now: float) -> None:
    """Drop expired flows. Caller holds ``_FLOWS_LOCK``."""
    for fid in [f for f, r in _FLOWS.items() if now >= r["expiry"]]:
        _FLOWS.pop(fid, None)


def _evict_for_user_locked(user_id: str) -> None:
    """Evict the oldest flow(s) for ``user_id`` down to the cap. Caller holds the lock."""
    owned = [(f, r) for f, r in _FLOWS.items() if r["user_id"] == user_id]
    if len(owned) < _MAX_FLOWS_PER_USER:
        return
    owned.sort(key=lambda fr: fr[1]["created_at"])
    for fid, _r in owned[: len(owned) - _MAX_FLOWS_PER_USER + 1]:
        _FLOWS.pop(fid, None)


def start_device_flow(
    user_id: str,
    *,
    provider: str,
    email: str,
    mailbox_label: str,
    host: str,
    port: int,
    smtp_host: str,
    smtp_port: int,
    import_scope: str = "last_30d",
    import_since=None,
    import_before=None,
) -> DeviceFlow:
    """Begin the Device Authorization Grant for ``provider``: POST client_id+scope
    to the device-auth endpoint, stash the in-flight record, and return the
    browser-safe fields. Raises :class:`OAuthDeviceError` on unknown provider /
    missing client_id / non-2xx / network / malformed response."""
    if provider not in _PROVIDER_DEVICE_AUTH:
        raise OAuthDeviceError(f"provider {provider!r} has no device flow")
    client_id = _client_id(provider)
    if not client_id:
        raise OAuthDeviceError(f"no client_id configured for {provider}")

    device_url = _PROVIDER_DEVICE_AUTH[provider]
    dev_host = _url_host(device_url)
    data = {"client_id": client_id, "scope": _PROVIDER_SCOPES[provider]}

    import httpx

    try:
        with oauth_egress_scope(dev_host, user_id=user_id):
            with httpx.Client(timeout=_DEVICE_TIMEOUT_SECONDS) as client:
                resp = client.post(device_url, data=data)
    except OAuthDeviceError:
        raise
    except Exception as exc:  # noqa: BLE001 - network/egress; message stays clean
        raise OAuthDeviceError(f"device-auth network error for {provider}") from exc

    if resp.status_code // 100 != 2:
        raise OAuthDeviceError(f"device-auth failed ({resp.status_code}) for {provider}")
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise OAuthDeviceError(f"device-auth returned non-JSON for {provider}") from exc

    device_code = body.get("device_code")
    user_code = body.get("user_code")
    # RFC 8628 spells it verification_uri; Google spells it verification_url.
    verification_uri = body.get("verification_uri") or body.get("verification_url")
    if not (
        isinstance(device_code, str)
        and device_code
        and isinstance(user_code, str)
        and user_code
        and isinstance(verification_uri, str)
        and verification_uri
    ):
        raise OAuthDeviceError(f"device-auth response missing fields for {provider}")

    interval = body.get("interval")
    interval = int(interval) if isinstance(interval, (int, float)) and interval > 0 else 5
    expires_in = body.get("expires_in")
    expires_in = (
        int(expires_in)
        if isinstance(expires_in, (int, float)) and expires_in > 0
        else _DEFAULT_DEVICE_EXPIRES_IN
    )
    vuc = body.get("verification_uri_complete")
    verification_uri_complete = vuc if isinstance(vuc, str) and vuc else None

    now = time.time()
    flow_id = secrets.token_urlsafe(32)
    record = {
        "device_code": device_code,
        "provider": provider,
        "user_id": user_id,
        "email": email,
        "mailbox_label": mailbox_label,
        "host": host,
        "port": port,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        # LUM-691 — import-scope parity: carried through to the route's `sources`
        # INSERT on authorize, same triple as the app-password create path.
        "import_scope": import_scope,
        "import_since": import_since,
        "import_before": import_before,
        "interval": interval,
        "created_at": now,
        "expiry": now + expires_in,
        "next_poll_at": now + interval,
        "in_progress": False,
    }
    with _FLOWS_LOCK:
        _prune_locked(now)
        _evict_for_user_locked(user_id)
        _FLOWS[flow_id] = record

    return DeviceFlow(
        flow_id=flow_id,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        interval=interval,
        expires_in=expires_in,
    )


def get_flow(user_id: str, flow_id: str) -> dict | None:
    """Return a COPY of the flow record owned by ``user_id`` (host/port/email/
    provider/mailbox_label the route needs to persist), or ``None``. Never
    exposes ``device_code``."""
    with _FLOWS_LOCK:
        rec = _FLOWS.get(flow_id)
        if rec is None or rec["user_id"] != user_id:
            return None
        return {
            k: rec[k]
            for k in (
                "provider",
                "email",
                "mailbox_label",
                "host",
                "port",
                "smtp_host",
                "smtp_port",
                "import_scope",
                "import_since",
                "import_before",
            )
        }


def discard_flow(flow_id: str) -> None:
    """Drop a flow after the route finishes persisting (success or hard-fail)."""
    with _FLOWS_LOCK:
        _FLOWS.pop(flow_id, None)


def poll_device_flow(user_id: str, flow_id: str) -> DevicePoll:
    """Poll the provider token endpoint at most once (honouring ``interval``/
    ``slow_down``). Owner-scoped. The token POST runs OUTSIDE the lock. On
    ``authorized`` the flow is left in place (``in_progress``) so a concurrent
    poll returns ``pending`` until the route calls :func:`discard_flow`."""
    now = time.time()
    with _FLOWS_LOCK:
        rec = _FLOWS.get(flow_id)
        if rec is None or rec.get("user_id") != user_id:
            return DevicePoll(status="not_found")
        if rec.get("in_progress"):
            return DevicePoll(status="pending")
        if now >= rec["expiry"]:
            _FLOWS.pop(flow_id, None)
            return DevicePoll(status="expired")
        if now < rec["next_poll_at"]:
            return DevicePoll(status="pending")
        rec["in_progress"] = True  # claim the poll slot; POST below is outside the lock
        provider = rec["provider"]
        device_code = rec["device_code"]

    def _release(*, drop: bool, bump: int = 0) -> None:
        with _FLOWS_LOCK:
            r = _FLOWS.get(flow_id)
            if r is None:
                return
            if drop:
                _FLOWS.pop(flow_id, None)
                return
            r["in_progress"] = False
            r["interval"] += bump
            r["next_poll_at"] = time.time() + r["interval"]

    client_id = _client_id(provider)
    if not client_id:
        _release(drop=True)
        raise OAuthDeviceError(f"no client_id configured for {provider}")

    token_url = _PROVIDER_TOKEN[provider]
    data = {"grant_type": _DEVICE_GRANT_TYPE, "device_code": device_code, "client_id": client_id}

    import httpx

    try:
        with oauth_egress_scope(_url_host(token_url), user_id=user_id):
            with httpx.Client(timeout=_DEVICE_TIMEOUT_SECONDS) as client:
                resp = client.post(token_url, data=data)
    except Exception as exc:  # noqa: BLE001
        _release(drop=False)
        raise OAuthDeviceError(f"device token network error for {provider}") from exc

    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {}

    if resp.status_code // 100 == 2:
        access_token = body.get("access_token")
        refresh_token = body.get("refresh_token")
        if not (isinstance(refresh_token, str) and refresh_token):
            _release(drop=True)
            raise OAuthDeviceError(f"device grant returned no refresh_token for {provider}")
        expires_in = body.get("expires_in")
        expires_in = (
            float(expires_in)
            if isinstance(expires_in, (int, float)) and expires_in > 0
            else _DEFAULT_EXPIRES_IN
        )
        # Keep the flow (in_progress) so a concurrent poll returns pending; the
        # route drops it via discard_flow after it persists.
        return DevicePoll(
            status="authorized",
            refresh_token=refresh_token,
            access_token=access_token if isinstance(access_token, str) else None,
            expiry=time.time() + expires_in,
        )

    err = body.get("error")
    if err == "authorization_pending":
        _release(drop=False)
        return DevicePoll(status="pending")
    if err == "slow_down":
        _release(drop=False, bump=_SLOW_DOWN_INCREMENT_SECONDS)
        return DevicePoll(status="pending")
    if err == "expired_token":
        _release(drop=True)
        return DevicePoll(status="expired")
    if err in ("access_denied", "authorization_declined", "bad_verification_code"):
        _release(drop=True)
        return DevicePoll(status="denied")
    _release(drop=True)
    raise OAuthDeviceError(f"device grant error ({resp.status_code}) for {provider}")
