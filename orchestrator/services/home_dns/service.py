# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Secure address at home — orchestration (LUM-331).

Turning it on: device key → ``/register`` → ``PUT /a`` → ``GET /budget`` →
render ``home.caddy.pending`` → stack-control validates it inside the Caddy
container → rename to ``home.caddy`` → graceful ``caddy reload``. Caddy then
obtains and renews the certificate itself (DNS-01, ARI). Every step is resumable:
a failure leaves ``enabled=True`` with ``last_error`` and a backoff, and
:func:`refresh_job` carries on from the first incomplete step.

Locking: state read-modify-write holds :data:`_lock`; network I/O never does. A
``generation`` counter, bumped by every admin action, discards results that a
later action has overtaken (for example a Worker reply arriving after Turn off).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Callable
from typing import Iterator

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from models.api_v1 import SecureAddressPublic
from models.api_v1 import SecureAddressStatus
from services.home_dns import state as st
from services.home_dns import validate
from services.home_dns import worker_client
from services.home_dns.snippet import render

from services import _atomic_file
from services import stack_control

_log = logging.getLogger(__name__)

_lock = threading.Lock()
ROUTE_LOCK_TIMEOUT_SEC = 2.0
# Serialises the enable sequence and the jobs (the 10 min job, the 30 s job and a
# route's background task are separate threads). Never taken by routes, and never
# held together with ``_lock`` except inside ``_commit``/``_apply_snippet``.
_progress_lock = threading.Lock()
PROGRESS_WAIT_SEC = 120.0
_OVERTAKEN = "overtaken"  # an admin action changed the generation mid-way: stop quietly
FAST_JOB_ID = "home_dns_fast"
SLOW_JOB_ID = "home_dns_refresh"
_BACKOFF_MINUTES = (1, 2, 4, 8, 16, 32, 60)
_DEREGISTER_GIVE_UP = timedelta(days=7)
_HEARTBEAT_EVERY = timedelta(days=1)
_PROBE_UNREACHABLE_WARN_AFTER = 3
# LUM-1004: at most one Caddy restart per this many seconds while the other CA's
# certificate is still served after a CA switch (Caddy obtains or loads in seconds).
_CA_RESTART_EVERY_SEC = 600
_last_ca_restart: float | None = None
_SELF_CLEARING = frozenset(
    {"stack_control_outdated", "caddy_volume_missing", "caddy_image_outdated"}
)
# A one-off run 60 s after boot when a setting changed while Core was down (LUM-996).
STARTUP_CHECK_JOB_ID = "home_dns_settings_check"
# Address detection. stack-control's ``/host-lan-address`` can take ``docker info``
# 10 s + ``docker ps`` 10 s + ``docker inspect`` 10 s + ``docker run`` 15 s = 45 s
# (``stack-control/main.py``), so the route waits for the whole sequence; the
# periodic job keeps the shorter limit because it runs under ``_progress_lock``.
DETECT_TIMEOUT_SEC = 50.0
JOB_DETECT_TIMEOUT_SEC = 20.0
DETECT_WAIT_SEC = 55.0
_DETECT_REUSE_SEC = 5.0
# monotonic; ``-inf`` so the first call never counts as "just finished".
_last_detect_done: float = float("-inf")
# Held around every helper call, so at most one is in flight from Core at a time.
_detect_lock = threading.Lock()
_render_warned = False


class Busy(Exception):
    """Another secure-address action holds the lock; the route answers 409."""


# --------------------------------------------------------------------------- config


def https_port() -> int:
    try:
        port = int(os.environ.get("LUMOGIS_SECURE_ADDRESS_PORT", "443"))
    except ValueError:
        return 443
    return port if 1 <= port <= 65535 else 443


def caddy_home_dir() -> Path:
    return Path(os.environ.get("CADDY_HOME_DIR", "/caddy-home"))


def _staging() -> bool:
    return os.environ.get("LUMOGIS_HOME_DNS_ACME_STAGING", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _probe_host() -> str:
    return os.environ.get("LUMOGIS_SECURE_ADDRESS_PROBE_HOST", "caddy")


def _probe_port() -> int:
    try:
        return int(os.environ.get("LUMOGIS_SECURE_ADDRESS_PROBE_PORT", "443"))
    except ValueError:
        return 443


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- locking


@contextmanager
def route_lock() -> Iterator[None]:
    if not _lock.acquire(timeout=ROUTE_LOCK_TIMEOUT_SEC):
        raise Busy()
    try:
        yield
    finally:
        _lock.release()


def _commit(generation: int, mutate: Callable[[st.HomeDnsState], None]) -> st.HomeDnsState | None:
    """Apply ``mutate`` to the saved state unless an admin action overtook us."""
    with _lock:
        current = st.load()
        if current.generation != generation:
            return None
        mutate(current)
        st.save(current)
        return current


def _fail(generation: int, code: str, retry_after: int | None = None) -> None:
    def mutate(s: st.HomeDnsState) -> None:
        now = _now()
        s.last_error = code
        s.last_error_at = now
        s.attempts += 1
        if retry_after is not None:
            delay = timedelta(seconds=retry_after)
        else:
            delay = timedelta(minutes=_BACKOFF_MINUTES[min(s.attempts, len(_BACKOFF_MINUTES)) - 1])
        s.next_retry_at = now + delay

    _commit(generation, mutate)


def _clear_error(s: st.HomeDnsState) -> None:
    s.last_error = None
    s.last_error_at = None
    s.attempts = 0
    s.next_retry_at = None


# --------------------------------------------------------------------------- device key


def _key_path() -> Path:
    return st.home_dir() / "device_ed25519.pem"


def load_or_create_device_key() -> tuple[Ed25519PrivateKey, bool]:
    """The device key and whether it was just created (``0600`` PKCS#8 PEM)."""
    path = _key_path()
    if path.is_file():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if isinstance(key, Ed25519PrivateKey):
            return key, False
        _log.error("home_dns: device key is not Ed25519; replacing it")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    st.home_dir().mkdir(parents=True, exist_ok=True)
    _atomic_file.atomic_write_text(path, pem, mode=0o600)
    return key, True


# --------------------------------------------------------------------------- stack-control


class _Outdated(Exception):
    pass


def _sidecar(method: str, path: str, timeout: float) -> dict:
    resp = (
        stack_control.post(path, timeout=timeout)
        if method == "POST"
        else stack_control.get(path, timeout=timeout)
    )
    if resp.status_code in (404, 405):
        raise _Outdated()
    resp.raise_for_status()
    return resp.json()


def detect_lan_address(timeout: float = JOB_DETECT_TIMEOUT_SEC) -> tuple[str | None, str | None]:
    """``(address, reason)`` from stack-control's host-network helper.

    The helper does a UDP ``connect()`` and sends no packet: it contacts nobody.
    """
    try:
        body = _sidecar("GET", "/host-lan-address", timeout)
    except _Outdated:
        return None, "failed"
    except Exception:
        return None, "failed"
    address = body.get("address")
    if isinstance(address, str) and validate.valid_lan_ipv4(address):
        return address, None
    reason = body.get("reason")
    return None, reason if reason in (
        "docker_desktop",
        "not_rfc1918",
        "failed",
        "helper_image_missing",
    ) else "failed"


def address_from_request(host: str | None) -> str | None:
    """An RFC1918 IPv4 literal the admin used to open Lumogis, or ``None``."""
    if not host:
        return None
    h = host.strip()
    name, sep, port = h.rpartition(":")
    if sep and port.isdigit():
        h = name
    return h if validate.valid_lan_ipv4(h) else None


def same_lan(a: str | None, b: str | None) -> bool:
    """Both valid IPv4 addresses in the same /24 (a DHCP lease change, not a new network)."""
    if not a or not b or not validate.valid_lan_ipv4(a) or not validate.valid_lan_ipv4(b):
        return False
    return ipaddress.IPv4Address(a).packed[:3] == ipaddress.IPv4Address(b).packed[:3]


def snippet_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _render_for(household_id: str, bearer: str) -> str:
    """The site block for the current settings; raises ``ValueError`` for invalid input."""
    return render(
        household_id, bearer, validate.server_url(), staging=_staging(), https_port=https_port()
    )


def _render_current(s: st.HomeDnsState) -> str | None:
    """The block the current settings would write, or ``None`` when it cannot be rendered."""
    global _render_warned
    if not s.household_id or not s.bearer:
        return None
    try:
        return _render_for(s.household_id, s.bearer)
    except ValueError:
        if not _render_warned:
            _render_warned = True
            _log.warning(
                "home_dns: the secure address settings render an invalid site block; "
                "the current block is left alone"
            )
        return None


def _applied_block_current(s: st.HomeDnsState) -> bool:
    """The block last applied is exactly what the current settings render."""
    if s.snippet_fingerprint is None:
        return False
    rendered = _render_current(s)
    return rendered is not None and snippet_fingerprint(rendered) == s.snippet_fingerprint


def _settings_changed(s: st.HomeDnsState) -> bool:
    return (
        s.enabled
        and s.snippet_applied_at is not None
        and s.last_error not in st.STICKY_ERRORS
        and _render_current(s) is not None
        and not _applied_block_current(s)
    )


def _snippet_paths() -> tuple[Path, Path]:
    home = caddy_home_dir() / "home.caddy"
    return home, home.with_name("home.caddy.pending")


def _remove_snippet() -> None:
    for path in _snippet_paths():
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _reload_caddy() -> str | None:
    """``None`` on success, else an error code."""
    try:
        body = _sidecar("POST", "/caddy/reload", 45)
    except _Outdated:
        return "stack_control_outdated"
    except Exception:
        return "caddy_unreachable"
    return None if body.get("ok") else "caddy_reload_failed"


def _restart_caddy() -> str | None:
    """Restart Caddy through stack-control (LUM-1004). ``None`` on success, else a code."""
    try:
        body = _sidecar("POST", "/caddy/restart", 90)
    except _Outdated:
        return "stack_control_outdated"
    except Exception:
        return "caddy_unreachable"
    return None if body.get("ok") else "caddy_restart_failed"


def _restart_for_other_ca() -> None:
    """The site for the requested CA is in place but Caddy still serves the other CA's
    certificate: a reload keeps a still-valid certificate in Caddy's cache (LUM-998
    step 0, 2026-11-18), so restart Caddy, at most once per
    ``_CA_RESTART_EVERY_SEC``. Called only after the probe, so the new block is
    already on disk and the restart loads it (restarting earlier reloads the old
    site)."""
    global _last_ca_restart
    now = time.monotonic()
    if _last_ca_restart is not None and now - _last_ca_restart < _CA_RESTART_EVERY_SEC:
        return
    _last_ca_restart = now
    _log.info("home_dns: the other CA's certificate is still served; restarting Caddy")
    err = _restart_caddy()
    if err is not None:
        _log.warning("home_dns: Caddy restart for the CA switch failed: %s", err)


def _set_fingerprint(generation: int, value: str | None) -> None:
    _commit(generation, lambda cur: setattr(cur, "snippet_fingerprint", value))


def _apply_snippet(
    generation: int, household_id: str, bearer: str, *, restore_on_failure: bool = True
) -> str | None:
    """Write → validate in Caddy → place → reload. ``None`` on success, else a code.

    The block is placed only if ``generation`` is still current and the address
    still on, checked under ``_lock`` (which :func:`disable` holds while it removes
    the files), so a Turn off during the slow validation always wins.

    When a block was already serving and the reload fails, that block is written
    back (Caddy's reload is atomic, so the old config is still the running one) and
    no second reload is issued. With no earlier block, or ``restore_on_failure``
    false (the block on disk is one Caddy refused at start), the file is removed
    and Caddy reloaded.
    """
    home, pending = _snippet_paths()
    if not caddy_home_dir().is_dir():
        return "caddy_volume_missing"
    text = _render_for(household_id, bearer)
    try:
        _atomic_file.atomic_write_text(pending, text, mode=0o640)
    except OSError:
        return "caddy_volume_missing"
    try:
        body = _sidecar("POST", "/caddy/apply", 45)
    except _Outdated:
        pending.unlink(missing_ok=True)
        return "stack_control_outdated"
    except Exception:
        pending.unlink(missing_ok=True)
        return "caddy_unreachable"
    if not body.get("ok"):
        pending.unlink(missing_ok=True)
        code = body.get("code")
        return (
            code
            if code in ("caddy_volume_missing", "caddy_image_outdated", "caddy_unreachable")
            else "caddy_snippet_rejected"
        )
    previous: str | None = None
    with _lock:
        current = st.load()
        if not current.enabled or current.generation != generation:
            pending.unlink(missing_ok=True)
            return _OVERTAKEN
        if restore_on_failure:
            try:
                previous = home.read_text(encoding="utf-8")
            except OSError:
                previous = None
        try:
            os.replace(pending, home)
        except FileNotFoundError:
            return _OVERTAKEN
    err = _reload_caddy()
    if err is None:
        _set_fingerprint(generation, snippet_fingerprint(text))
        return None
    if previous is not None and err == "caddy_reload_failed":
        # Caddy refused the new config (``ok: false``), so the old one is still the
        # running one: put the old block back, and never after a Turn off. Any other
        # error (a timeout, an unreachable sidecar) does not prove Caddy kept the old
        # config, so it falls through to the remove path below.
        with _lock:
            current = st.load()
            if not current.enabled or current.generation != generation:
                home.unlink(missing_ok=True)
                return _OVERTAKEN
            try:
                _atomic_file.atomic_write_text(home, previous, mode=0o640)
                return err  # disk equals the running config again; the fingerprint still fits
            except OSError:
                pass
    # Keep disk equal to the running config: a restart must not load it either.
    _remove_snippet()
    _reload_caddy()
    _set_fingerprint(generation, None)
    return err


# --------------------------------------------------------------------------- checks


def probe_certificate(name: str) -> tuple[str, datetime | None, datetime | None]:
    """``(kind, not_before, not_after)``; kind ∈ ``ok`` / ``other_ca`` / ``no_cert`` /
    ``unreachable``. ``other_ca``: a valid certificate for the name, issued by the
    other CA than ``LUMOGIS_HOME_DNS_ACME_STAGING`` asks for."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # presence check, not trust: Caddy is on our network
    try:
        with socket.create_connection((_probe_host(), _probe_port()), timeout=3) as sock:
            try:
                with ctx.wrap_socket(sock, server_hostname=name) as tls:
                    der = tls.getpeercert(binary_form=True)
            except (ssl.SSLEOFError, ssl.SSLZeroReturnError):
                # Closed mid-handshake (e.g. Caddy restarting): keep the cache.
                return "unreachable", None, None
            except ssl.SSLError:
                # A TLS alert: Caddy has no certificate for this name.
                return "no_cert", None, None
    except OSError:
        return "unreachable", None, None
    if not der:
        return "no_cert", None, None
    cert = x509.load_der_x509_certificate(der)
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names = [n.lower() for n in sans.value.get_values_for_type(x509.DNSName)]
    except x509.ExtensionNotFound:
        names = []
    if name.lower() not in names:
        return "no_cert", None, None
    # Let's Encrypt staging names every issuer "(STAGING) ...".
    staging_leaf = "(STAGING)" in cert.issuer.rfc4514_string()
    kind = "other_ca" if staging_leaf != _staging() else "ok"
    return kind, cert.not_valid_before_utc, cert.not_valid_after_utc


def resolver_check(name: str, expected_ip: str, deadline: float = 2.0) -> str:
    """``ok`` / ``blocked`` / ``pending`` / ``unknown`` via this host's resolver.

    The DNS rcode is not used to tell a cached "no such name" from a router that
    hides the name: rebind filters answer differently (timeout, SERVFAIL, an empty
    or stripped answer), so time decides instead (``state.NAME_NEW_WINDOW``, LUM-996).
    """

    def lookup() -> list[str]:
        infos = socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM)
        return [str(i[4][0]) for i in infos]

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(lookup)
    try:
        ips = future.result(timeout=deadline)
    except FutureTimeout:
        return "unknown"
    except socket.gaierror:
        return "blocked"
    except Exception:
        return "unknown"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if not ips:
        return "blocked"
    return "ok" if expected_ip in ips else "pending"


# --------------------------------------------------------------------------- progress


def _register(generation: int, s: st.HomeDnsState) -> st.HomeDnsState | None:
    key, _created = load_or_create_device_key()
    result = worker_client.register(key)

    def mutate(cur: st.HomeDnsState) -> None:
        now = _now()
        if cur.household_id and cur.household_id != result.household_id:
            cur.previous_household_id = cur.household_id  # new key → new name
        day = now.strftime("%Y-%m-%d")
        if cur.registered_at is not None:  # a re-registration, not the first
            cur.re_registrations_today = (
                cur.re_registrations_today + 1 if cur.re_registrations_day == day else 1
            )
            cur.re_registrations_day = day
            if cur.re_registrations_today > 3 and "two_computers" not in cur.warnings:
                cur.warnings.append("two_computers")
        cur.household_id = result.household_id
        cur.bearer = result.bearer
        cur.registered_at = now
        cur.last_a_update_at = None  # the fresh registration needs its A record
        cur.snippet_applied_at = None  # and the snippet must carry the new bearer
        _clear_error(cur)

    return _commit(generation, mutate)


def _advance(generation: int) -> None:
    """A route's background task: wait for a running job, then advance. Never raises."""
    if not _progress_lock.acquire(timeout=PROGRESS_WAIT_SEC):
        _log.warning("home_dns: a job held the secure address for too long; the next run continues")
        return
    try:
        _advance_locked(generation)
    finally:
        _progress_lock.release()


def _advance_locked(generation: int) -> None:
    """Run the enable sequence from the first incomplete step (caller holds
    ``_progress_lock``). Never raises."""
    try:
        _advance_inner(generation)
    except worker_client.WorkerError as exc:
        _fail(generation, exc.code, exc.retry_after)
    except Exception:
        _log.exception("home_dns: unexpected failure while turning the secure address on")
        _fail(generation, "worker_unreachable")


def _advance_inner(generation: int) -> None:
    s = st.load()
    if not s.enabled or s.generation != generation or not s.lan_address:
        return
    if not s.registered_at or not s.bearer:
        s = _register(generation, s)
        if s is None:
            return
    if s.last_a_update_at is None:
        try:
            worker_client.put_a(s.bearer, s.lan_address)
        except worker_client.BearerRejected:
            s = _register(generation, s)
            if s is None:
                return
            worker_client.put_a(s.bearer, s.lan_address)

        def a_published(cur: st.HomeDnsState) -> None:
            now = _now()
            cur.last_a_update_at = now
            # The name is published once per registration: an address change, a CA
            # switch or a re-apply never moves the "the name is new" anchor.
            if cur.name_published_at is None or (
                cur.registered_at is not None and cur.name_published_at < cur.registered_at
            ):
                cur.name_published_at = now
                if (
                    cur.registered_at is not None
                    and now - cur.registered_at > st.NAME_NEW_WINDOW
                    and cur.snippet_applied_at is not None
                ):
                    # A state file from before LUM-996 has no ``name_published_at``,
                    # but its name has served for longer than the window: an upgrade
                    # must not show "the name is new" (LUM-1004, seen live 2026-11-18).
                    cur.name_published_at = cur.registered_at

        s = _commit(generation, a_published)
        if s is None:
            return
    if s.snippet_applied_at is None:
        budget = worker_client.budget(s.bearer)
        if budget.first_cert_needed and not budget.available:

            def note_budget(cur: st.HomeDnsState) -> None:
                if "budget_exhausted" not in cur.warnings:
                    cur.warnings.append("budget_exhausted")

            _commit(generation, note_budget)
            raise worker_client.WorkerError("budget_exhausted", retry_after=budget.retry_after)
        err = _apply_snippet(generation, s.household_id, s.bearer)
        if err == _OVERTAKEN:
            return
        if err is not None:
            raise worker_client.WorkerError(err)

        def applied(cur: st.HomeDnsState) -> None:
            cur.snippet_applied_at = _now()
            if "budget_exhausted" in cur.warnings:
                cur.warnings.remove("budget_exhausted")
            _clear_error(cur)

        _commit(generation, applied)
        ensure_fast_job()


# --------------------------------------------------------------------------- admin actions


def _source_for(address: str, request_host: str | None) -> str:
    s = st.load()
    if address == s.last_detected_address:
        return "detected"
    if address == address_from_request(request_host):
        return "request"
    return "manual"


def enable(lan_address: str, request_host: str | None) -> int:
    """Mark enabled (caller holds :func:`route_lock`); returns the new generation."""
    s = st.load()
    s.generation += 1
    s.enabled = True
    s.enabled_at = _now()
    s.lan_address = lan_address
    s.lan_address_source = _source_for(lan_address, request_host)
    s.pending_deregister = False  # re-registering supersedes any owed delete
    s.pending_deregister_since = None
    s.snippet_applied_at = None
    s.last_a_update_at = None
    _clear_error(s)
    s.warnings = []
    st.save(s)
    return s.generation


def set_lan_address(lan_address: str, request_host: str | None) -> int:
    s = st.load()
    s.generation += 1
    s.lan_address = lan_address
    s.lan_address_source = _source_for(lan_address, request_host)
    s.last_a_update_at = None
    if s.last_error == "lan_address_unknown":
        _clear_error(s)  # the admin's new address is the fix
    if "address_mismatch" in s.warnings:
        s.warnings.remove("address_mismatch")
    st.save(s)
    return s.generation


def retry() -> int:
    s = st.load()
    s.generation += 1
    _clear_error(s)
    st.save(s)
    return s.generation


def disable() -> int:
    """Turn off locally (works offline); the Worker delete is owed until it succeeds.

    The caller holds :func:`route_lock`; it calls :func:`after_disable` once the
    lock is released, so the Caddy reload never runs under the lock.
    """
    s = st.load()
    s.generation += 1
    s.enabled = False
    s.snippet_applied_at = None
    s.snippet_fingerprint = None
    s.cert_not_before = s.cert_not_after = None
    if s.bearer:
        s.pending_deregister = True
        s.pending_deregister_since = _now()
    _clear_error(s)
    s.warnings = []
    st.save(s)
    _remove_snippet()
    return s.generation


def after_disable() -> None:
    """Reload Caddy without the household block, then try the owed Worker delete."""
    _reload_caddy()
    try_deregister()


def try_deregister() -> None:
    s = st.load()
    if s.enabled or not s.pending_deregister or not s.bearer:
        return
    generation = s.generation
    try:
        worker_client.deregister(s.bearer)
        done = True
    except worker_client.WorkerError:
        done = False
        since = s.pending_deregister_since or _now()
        if _now() - since > _DEREGISTER_GIVE_UP:
            _log.warning("home_dns: giving up the owed Worker delete after 7 days")
            done = True

    if done:

        def cleared(cur: st.HomeDnsState) -> None:
            cur.pending_deregister = False
            cur.pending_deregister_since = None
            cur.bearer = None
            cur.registered_at = None

        _commit(generation, cleared)


# --------------------------------------------------------------------------- jobs


def ensure_fast_job() -> None:
    """A 30 s job while registering/issuing, removed once ready or after 15 min."""
    try:
        import config

        scheduler = config.get_scheduler()
        scheduler.add_job(
            _fast_tick,
            trigger="interval",
            seconds=30,
            id=FAST_JOB_ID,
            name="Secure address: follow issuance",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    except Exception:
        _log.debug("home_dns: fast job not scheduled", exc_info=True)


def _remove_fast_job() -> None:
    try:
        import config

        config.get_scheduler().remove_job(FAST_JOB_ID)
    except Exception:
        pass


def _fast_tick() -> None:
    refresh_job(fast=True)
    s = st.load()
    view = st.derive(s, _now(), sign_in_enabled=False, https_port=https_port())
    started = s.snippet_applied_at or s.enabled_at
    if view.state not in ("registering", "issuing") or (
        started is not None and _now() - started > st.CERTIFICATE_PENDING_AFTER
    ):
        _remove_fast_job()


def on_startup() -> None:
    """Resume the fast job after a restart mid-enable, and re-check the settings.

    A changed CA, name-service URL, port or template is applied by a one-off run 60 s
    after boot, not inline: Caddy may not be up yet at Core boot, and an inline
    re-apply would then fail against a site that is still serving.
    """
    s = st.load()
    view = st.derive(s, _now(), sign_in_enabled=False, https_port=https_port())
    if view.state in ("registering", "issuing"):
        ensure_fast_job()
    if _settings_changed(s):
        try:
            import config

            config.get_scheduler().add_job(
                refresh_when_free,
                trigger="date",
                run_date=_now() + timedelta(seconds=60),
                id=STARTUP_CHECK_JOB_ID,
                name="Secure address: apply changed settings",
                replace_existing=True,
            )
        except Exception:
            _log.debug("home_dns: settings check not scheduled", exc_info=True)


def refresh_job(fast: bool = False) -> None:
    """Periodic upkeep (every 10 min; 30 s while issuing). Never raises.

    Skips the run when another one (or a route's background task) is still busy.
    The fast run only advances and probes: no address detection, no Caddy
    recovery check, no heartbeat.
    """
    if not _progress_lock.acquire(blocking=False):
        return
    try:
        _refresh_inner(fast)
    except Exception:
        _log.exception("home_dns: refresh job failed")
    finally:
        _progress_lock.release()


def refresh_when_free() -> None:
    """Like :func:`refresh_job`, but waits for a running job instead of skipping.

    Used by the start-up check and ``POST /retry``, which must not be dropped just
    because the fast or the 10-minute job holds the lock. Never raises.
    """
    if not _progress_lock.acquire(timeout=PROGRESS_WAIT_SEC):
        _log.warning("home_dns: a job held the secure address for too long; the next run continues")
        return
    try:
        _refresh_inner(False)
    except Exception:
        _log.exception("home_dns: refresh failed")
    finally:
        _progress_lock.release()


def _remove_stale_snippet() -> None:
    """Off (or reset) but a block is still on disk: a reload failed, or the
    process stopped between Turn off and the reload. Remove it and reload."""
    if any(p.exists() for p in _snippet_paths()):
        _remove_snippet()
        _reload_caddy()


def _reapply_if_changed(
    generation: int, s: st.HomeDnsState, now: datetime, home_status: dict
) -> None:
    """Step 5b: apply the site again when what it renders has changed. Never takes a
    working site down: a refused or failed re-apply keeps (or restores) the old block."""
    if (
        s.last_error == "caddy_snippet_rejected"
        and s.snippet_applied_at is not None
        and home_status.get("present") is True
        and _applied_block_current(s)
    ):
        # The setting that was refused has been reverted: the block on disk is the
        # one the current settings render.
        s = _commit(generation, _clear_error) or s
    if not _settings_changed(s):
        return
    if s.next_retry_at is not None and now < s.next_retry_at:
        return
    _log.info(
        "home_dns: settings changed (CA, name-service URL, port or Lumogis version); "
        "re-applying the site in place"
    )
    err = _apply_snippet(generation, s.household_id, s.bearer)
    if err == _OVERTAKEN:
        return
    if err is None:

        def applied(cur: st.HomeDnsState) -> None:
            _clear_error(cur)
            if "budget_exhausted" in cur.warnings:
                cur.warnings.remove("budget_exhausted")
            cur.snippet_applied_at = _now()

        _commit(generation, applied)
        ensure_fast_job()
        return
    # Note: a refusal is the sticky ``caddy_snippet_rejected``, and sticky errors stop
    # step 2, so a DHCP change followed meanwhile is not pushed (``put_a``) until the
    # admin presses Retry or reverts the setting (5b self-clears). Accepted (LUM-996).
    _fail(generation, err)
    home, _pending = _snippet_paths()
    if not home.exists():
        # The write-back itself failed: the normal enable path re-applies, with
        # backoff and the budget check.
        _commit(generation, lambda cur: setattr(cur, "snippet_applied_at", None))


def _refresh_inner(fast: bool = False) -> None:
    now = _now()
    s = st.load()

    if s.last_error == "state_corrupt":
        _remove_stale_snippet()
        return

    if not s.enabled:
        _remove_stale_snippet()
        try_deregister()
        return

    generation = s.generation
    if s.last_error in _SELF_CLEARING:
        _commit(generation, _clear_error)
        s = st.load()

    # 1. Address: a detected address follows the host within its own /24; anything
    #    else only warns. A failed detection changes nothing but the reason (a
    #    transient failure must not clear an address-mismatch warning). If a route is
    #    detecting right now, skip detection and ``note_detection`` for this run: it
    #    records its own result.
    global _last_detect_done
    address = reason = None

    def note_detection(cur: st.HomeDnsState) -> None:
        cur.last_detection_reason = reason
        if address is None:
            return
        cur.last_detected_address = address
        if (
            cur.lan_address_source == "detected"
            and address != cur.lan_address
            and same_lan(address, cur.lan_address)
        ):
            cur.lan_address = address
            cur.last_a_update_at = None
        mismatch = (
            cur.lan_address is not None
            and address != cur.lan_address
            and (
                cur.lan_address_source in ("manual", "request")
                or not same_lan(address, cur.lan_address)
            )
        )
        if mismatch and "address_mismatch" not in cur.warnings:
            cur.warnings.append("address_mismatch")
        elif not mismatch and "address_mismatch" in cur.warnings:
            cur.warnings.remove("address_mismatch")

    if not fast and _detect_lock.acquire(blocking=False):
        try:
            address, reason = detect_lan_address(JOB_DETECT_TIMEOUT_SEC)
            if reason != "failed":
                _last_detect_done = time.monotonic()
            # Recorded while still holding the lock, so a ``detect_now`` waiting on it
            # returns after this result is in the state (lock order as in detect_now).
            s = _commit(generation, note_detection) or s
        finally:
            _detect_lock.release()

    # 2. Progress (registration, A record, snippet) unless backing off.
    if s.next_retry_at is None or now >= s.next_retry_at:
        if s.last_error not in st.STICKY_ERRORS:
            _advance_locked(generation)
            s = st.load()

    if not s.household_id or not validate.valid_household_id(s.household_id):
        return
    name = validate.name_for(s.household_id)

    # 3. Certificate probe (cache kept on "unreachable").
    kind, not_before, not_after = probe_certificate(name)

    # A certificate from the other CA is dropped only once the block for the requested
    # CA is in place; until then it is still what is served, and browsers accept it.
    applied = _applied_block_current(s) if kind == "other_ca" else False

    def note_probe(cur: st.HomeDnsState) -> None:
        if kind == "ok" or (kind == "other_ca" and not applied):
            cur.cert_not_before, cur.cert_not_after = not_before, not_after
            cur.probe_unreachable_count = 0
        elif kind in ("no_cert", "other_ca"):
            cur.cert_not_before = cur.cert_not_after = None
            cur.probe_unreachable_count = 0
        else:
            cur.probe_unreachable_count += 1
        unreachable = cur.probe_unreachable_count >= _PROBE_UNREACHABLE_WARN_AFTER
        if unreachable and "caddy_unreachable" not in cur.warnings:
            cur.warnings.append("caddy_unreachable")
        elif not unreachable and "caddy_unreachable" in cur.warnings:
            cur.warnings.remove("caddy_unreachable")

    s = _commit(generation, note_probe) or s
    if kind == "other_ca" and applied:
        _restart_for_other_ca()

    # 4. Router check (this host's resolver; the device check in Web is decisive).
    if s.lan_address:
        verdict = resolver_check(name, s.lan_address)

        def note_resolver(cur: st.HomeDnsState) -> None:
            cur.resolver = verdict
            cur.resolver_checked_at = _now()

        s = _commit(generation, note_resolver) or s

    if fast:
        return

    # 5. Fallback recovery: Caddy started without the household block.
    try:
        home_status = _sidecar("GET", "/caddy/home-status", 45)
    except Exception:
        home_status = {}
    if (
        home_status.get("reachable")
        and home_status.get("present") is False
        and s.snippet_fingerprint is not None
    ):
        # Caddy cannot see a block the state says is applied (a wiped or unrestored
        # volume): forget the fingerprint so step 5b re-applies it in this run.
        s = _commit(generation, lambda cur: setattr(cur, "snippet_fingerprint", None)) or s
    if home_status.get("rejected_at_start") and s.bearer and s.household_id:
        # The block on disk is the one Caddy refused at start, so there is nothing
        # running to restore: restore_on_failure=False.
        err = _apply_snippet(generation, s.household_id, s.bearer, restore_on_failure=False)
        if err == _OVERTAKEN:
            return
        if err is not None:
            _remove_snippet()
            _reload_caddy()
            _set_fingerprint(generation, None)
            _fail(generation, "caddy_snippet_rejected")
            return

    # 5b. Settings changed (CA, name-service URL, port, template): re-apply in place.
    #     Fresh state: step 5 does not refresh ``s``.
    _reapply_if_changed(generation, st.load(), now, home_status)
    s = st.load()

    # 6. Daily heartbeat: a dead bearer (restore, clone) is found early. Stamped
    #    only when it got an answer; a failed re-registration backs off like any
    #    other Worker error instead of repeating every run.
    backing_off = s.next_retry_at is not None and now < s.next_retry_at
    due = s.last_heartbeat_at is None or now - s.last_heartbeat_at > _HEARTBEAT_EVERY
    if s.bearer and due and not backing_off:
        answered = False
        try:
            worker_client.budget(s.bearer)
            answered = True
        except worker_client.BearerRejected:
            try:
                if _register(generation, s) is not None:
                    _advance_locked(generation)
                answered = True
            except worker_client.WorkerError as exc:
                _fail(generation, exc.code, exc.retry_after)
        except worker_client.WorkerError:
            pass
        if answered:
            _commit(generation, lambda cur: setattr(cur, "last_heartbeat_at", now))


# --------------------------------------------------------------------------- views


def detect_now() -> None:
    """Run the host-address helper now and record its result (``POST /detect``).

    Callable in every state. It only records the detection (``last_detected_address``
    on success, ``last_detection_reason`` always): no generation bump, and no change
    to ``enabled``, ``lan_address`` or ``warnings``. While the feature is on, the next
    ``refresh_job`` applies the usual rules for a detected address. One helper call is
    in flight at a time; a caller that had to wait reuses the holder's result, and a
    result less than 5 s old (not a failure) is reused. Raises :class:`Busy`.
    """
    global _last_detect_done
    waited = not _detect_lock.acquire(blocking=False)
    if waited and not _detect_lock.acquire(timeout=DETECT_WAIT_SEC):
        raise Busy()
    try:
        if waited:
            return  # the holder has just detected and recorded its result, failed or not
        if time.monotonic() - _last_detect_done < _DETECT_REUSE_SEC:
            return
        address, reason = detect_lan_address(DETECT_TIMEOUT_SEC)
        with route_lock():
            cur = st.load()
            cur.last_detection_reason = reason
            if address is not None:
                cur.last_detected_address = address
            st.save(cur)
        if reason != "failed":
            _last_detect_done = time.monotonic()
    finally:
        _detect_lock.release()


def status(request_host: str | None, *, sign_in_enabled: bool) -> SecureAddressStatus:
    s = st.load()
    # An RFC 1918 Host is the address the admin's own device just reached, so it
    # wins; detection fills in when the Host is not one (``localhost``, a name). A
    # failed detection never clears an earlier good address (it is only written on
    # success).
    suggested = address_from_request(request_host)
    if (
        suggested is None
        and s.last_detected_address
        and validate.valid_lan_ipv4(s.last_detected_address)
    ):
        suggested = s.last_detected_address
    if suggested == s.lan_address:
        suggested = None
    return st.derive(
        s,
        _now(),
        sign_in_enabled=sign_in_enabled,
        https_port=https_port(),
        suggested_address=suggested,
    )


def summary_state() -> tuple[str, str | None]:
    s = st.load()
    view = st.derive(s, _now(), sign_in_enabled=False, https_port=https_port())
    return view.state, st.public_url(s, _now(), https_port())


def public_view() -> SecureAddressPublic:
    s = st.load()
    return SecureAddressPublic(url=st.public_url(s, _now(), https_port()))


def audit(action: str, user_id: str, summary: dict) -> None:
    """One ``audit_log`` row; failures are logged, never raised."""
    try:
        from actions.audit import AuditEntry
        from actions.audit import write_audit

        write_audit(
            AuditEntry(
                action_name=action,
                connector="admin",
                mode="system",
                input_summary=json.dumps(summary, default=str),
                result_summary=json.dumps({}, default=str),
                executed_at=_now(),
                user_id=user_id,
            )
        )
    except Exception:
        _log.exception("home_dns: audit write failed for %s", action)
