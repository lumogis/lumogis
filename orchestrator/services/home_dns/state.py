# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Persisted secure-address state and the pure state machine (LUM-331).

``{workspace}/home-dns/state.json`` (``0600``) holds everything the service needs
between runs. The workspace is in every DR backup, so a restore keeps the device
key and therefore the household's name. :func:`derive` turns a state into what
the admin card shows; it is pure and takes ``now`` so tests can drive it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Literal
from typing import Optional

from models.api_v1 import SecureAddressErrorCode
from models.api_v1 import SecureAddressStatus
from models.api_v1 import SecureAddressWarning
from pydantic import BaseModel
from pydantic import Field
from pydantic import ValidationError
from services.home_dns import validate

import config
from services import _atomic_file

_log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
CERTIFICATE_PENDING_AFTER = timedelta(minutes=15)
# After a (re)registration a resolver may still hold a cached "no such name". The
# ``lumogis.app`` zone's SOA minimum is 1800 s (RFC 2308 negative caching; read with
# ``dig +short SOA lumogis.app`` on 2026-11-18: ``... 10000 2400 604800 1800``) plus
# margin. Re-derive this if the zone's SOA changes.
NAME_NEW_WINDOW = timedelta(minutes=35)

# Errors that stop progress until the admin acts or an upgrade clears them.
STICKY_ERRORS: frozenset[str] = frozenset(
    {
        "caddy_image_outdated",
        "service_full",
        "caddy_volume_missing",
        "caddy_snippet_rejected",
        "stack_control_outdated",
        "registration_refused",
        "registration_retired",
        "clock_skew",
        "lan_address_unknown",  # the Worker refused the address: the admin types another
    }
)
# Errors that only overlay "still getting there" while the service retries.
_PROGRESS_ERRORS: frozenset[str] = frozenset(
    {
        "worker_unreachable",
        "registration_rate_limited",
        "budget_exhausted",
        "caddy_unreachable",
        "caddy_reload_failed",
    }
)


class HomeDnsState(BaseModel):
    schema_version: int = SCHEMA_VERSION
    generation: int = 0
    enabled: bool = False
    household_id: Optional[str] = None
    bearer: Optional[str] = None
    lan_address: Optional[str] = None
    lan_address_source: Optional[Literal["detected", "request", "manual"]] = None
    enabled_at: Optional[datetime] = None
    registered_at: Optional[datetime] = None
    snippet_applied_at: Optional[datetime] = None
    last_a_update_at: Optional[datetime] = None
    cert_not_before: Optional[datetime] = None
    cert_not_after: Optional[datetime] = None
    resolver: Literal["ok", "blocked", "pending", "unknown"] = "unknown"
    # When ``resolver`` was last stored: a verdict taken inside the "name is new"
    # window can be the cached miss, so it never counts as router_blocked (LUM-996).
    resolver_checked_at: Optional[datetime] = None
    # sha256 hex of ``home.caddy`` as last applied and reloaded. Set only while the
    # block on disk is exactly that text; every path that removes the block clears it.
    snippet_fingerprint: Optional[str] = None
    # The first ``PUT /a`` after the current registration: the "name is new" anchor.
    name_published_at: Optional[datetime] = None
    last_error: Optional[SecureAddressErrorCode] = None
    last_error_at: Optional[datetime] = None
    attempts: int = 0
    next_retry_at: Optional[datetime] = None
    pending_deregister: bool = False
    pending_deregister_since: Optional[datetime] = None
    last_detection_reason: Optional[
        Literal["docker_desktop", "not_rfc1918", "failed", "helper_image_missing"]
    ] = None
    last_detected_address: Optional[str] = None
    previous_household_id: Optional[str] = None
    probe_unreachable_count: int = 0
    re_registrations_today: int = 0
    re_registrations_day: Optional[str] = None
    last_heartbeat_at: Optional[datetime] = None
    warnings: list[SecureAddressWarning] = Field(default_factory=list)


def home_dir() -> Path:
    return config.get_workspace_path() / "home-dns"


def state_path() -> Path:
    return home_dir() / "state.json"


def _ensure_dir() -> Path:
    d = home_dir()
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def save(state: HomeDnsState) -> None:
    _ensure_dir()
    _atomic_file.atomic_write_text(state_path(), state.model_dump_json(indent=2), mode=0o600)


def load(now: datetime | None = None) -> HomeDnsState:
    """The saved state; a default when none exists; quarantine when corrupt."""
    path = state_path()
    if not path.is_file():
        return HomeDnsState()
    try:
        return HomeDnsState.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError):
        now = now or datetime.now(timezone.utc)
        quarantine = path.with_name(f"state.json.corrupt-{now.strftime('%Y%m%dT%H%M%SZ')}")
        try:
            path.rename(quarantine)
        except OSError:
            _log.exception("home_dns: could not quarantine corrupt state.json")
        _log.error("home_dns: state.json was corrupt; moved to %s", quarantine.name)
        fresh = HomeDnsState(last_error="state_corrupt", last_error_at=now)
        save(fresh)
        return fresh


def secure_url(household_id: str | None, https_port: int) -> str | None:
    if not household_id or not validate.valid_household_id(household_id):
        return None
    name = validate.name_for(household_id)
    return f"https://{name}" if https_port == 443 else f"https://{name}:{https_port}"


def certificate_present(state: HomeDnsState, now: datetime) -> bool:
    return state.cert_not_after is not None and state.cert_not_after > now


def public_url(state: HomeDnsState, now: datetime, https_port: int) -> str | None:
    """What members may see: set while enabled with an unexpired cached certificate.

    Independent of error overlays and stable across Caddy restarts (an unreachable
    probe keeps the cache), so LUM-988's QR code does not flicker.
    """
    if not state.enabled or not state.registered_at or not certificate_present(state, now):
        return None
    return secure_url(state.household_id, https_port)


def name_new_window_end(state: HomeDnsState) -> datetime | None:
    """When the "the name is new" window ends, or ``None`` (off, or no anchor).

    The anchor is the later of ``registered_at`` and ``name_published_at``.
    ``snippet_applied_at`` is deliberately not an anchor: a CA switch resets it, and
    that must not restart "the name is new" for a name that is days old.
    """
    if not state.enabled:
        return None
    anchors = [a for a in (state.registered_at, state.name_published_at) if a is not None]
    if not anchors:
        return None
    return max(anchors) + NAME_NEW_WINDOW


def name_new_until(state: HomeDnsState, now: datetime) -> datetime | None:
    """The window end while it is still in the future, else ``None``."""
    end = name_new_window_end(state)
    return end if end is not None and end > now else None


def blocked_after_window(state: HomeDnsState) -> bool:
    """A "blocked" resolver verdict that was taken once the window had ended.

    A verdict stored at minute 25-34 can still be the cached miss, so it never
    counts. A state file from before this field existed has no ``resolver_checked_at``
    and shows ``ready`` until the next run stores a fresh verdict.
    """
    if state.resolver != "blocked" or state.resolver_checked_at is None:
        return False
    end = name_new_window_end(state)
    return end is None or state.resolver_checked_at >= end


def derive(
    state: HomeDnsState,
    now: datetime,
    *,
    sign_in_enabled: bool,
    https_port: int = 443,
    suggested_address: str | None = None,
    detection_unavailable_reason: str | None = None,
) -> SecureAddressStatus:
    """The admin card's view of ``state``. First matching row wins (plan table)."""
    has_cert = certificate_present(state, now)
    error_code: str | None = None

    if state.last_error == "state_corrupt":  # row 0
        status = "error"
        error_code = "state_corrupt"
    elif not state.enabled:  # row 1
        status = "off"
    elif state.last_error in STICKY_ERRORS:  # row 2
        status = "error"
        error_code = state.last_error
    elif not state.lan_address:  # row 3
        status = "needs_address"
    elif not state.registered_at:  # row 4
        status = "registering"
        error_code = state.last_error if state.last_error in _PROGRESS_ERRORS else None
    elif not has_cert and state.cert_not_after is not None:  # row 7 (expired)
        status = "error"  # we had one and it lapsed: renewal failed, not "pending"
        error_code = "renewal_failing"
    elif not has_cert:
        applied = state.snippet_applied_at
        if applied is not None and now - applied > CERTIFICATE_PENDING_AFTER:  # row 5
            status = "error"
            error_code = "certificate_pending"
        else:  # row 6
            status = "issuing"
            error_code = state.last_error if state.last_error in _PROGRESS_ERRORS else None
    else:
        lifetime = (
            state.cert_not_after - state.cert_not_before
            if state.cert_not_before is not None
            else None
        )
        remaining = state.cert_not_after - now
        if lifetime is not None and lifetime.total_seconds() > 0 and remaining < lifetime / 6:
            status = "error"  # row 7
            error_code = "renewal_failing"
        elif blocked_after_window(state):  # row 8
            status = "router_blocked"
        else:  # row 9
            status = "ready"
        if status in ("ready", "router_blocked") and state.last_error in _PROGRESS_ERRORS:
            error_code = state.last_error  # row 9: a warning on the card, not a state change

    retry_after = None
    if state.next_retry_at is not None and state.next_retry_at > now:
        retry_after = int((state.next_retry_at - now).total_seconds())

    return SecureAddressStatus(
        state=status,
        name=validate.name_for(state.household_id)
        if state.household_id and validate.valid_household_id(state.household_id)
        else None,
        url=secure_url(state.household_id, https_port) if state.enabled else None,
        lan_address=state.lan_address,
        lan_address_source=state.lan_address_source,
        suggested_address=suggested_address,
        detection_unavailable_reason=detection_unavailable_reason or state.last_detection_reason,
        certificate_valid_until=state.cert_not_after if has_cert else None,
        resolver=state.resolver,
        error_code=error_code,
        retry_after_seconds=retry_after,
        name_new_until=name_new_until(state, now),
        name_changed=state.previous_household_id is not None,
        sign_in_enabled=sign_in_enabled,
        warnings=list(state.warnings),
    )
