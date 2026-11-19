# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP client for the Lumogis Home DNS Worker (LUM-331).

Contract (ADR 115; LUM-521 plan "Data contracts", plus the LUM-331 additions):

* ``POST /register`` ``{pubkey, registration_secret, ts, sig}`` → ``201 {household_id,
  name, token}``. ``sig`` is Ed25519 over the UTF-8 bytes ``"register|{pubkey}|{ts}"``;
  ``pubkey`` and ``sig`` are base64url **without padding**; ``ts`` is decimal Unix
  seconds (±300 s). Every call returns a **fresh** bearer and invalidates the old one.
* ``PUT /a`` (Bearer) ``{ip}`` → ``200``; RFC1918 only.
* ``GET /budget`` (Bearer) → ``{first_cert_needed, available, scope, retry_after_seconds}``.
* ``DELETE /register`` (Bearer) → ``204``.
* Errors: ``{"error": "<code>", "message": "..."}``.

The registration secret is public by design (it ships in AGPL code); abuse is
bounded by the Worker's budgets, not by the secret. Nothing here logs the bearer.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Final

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.hazmat.primitives.serialization import PublicFormat
from services.home_dns import validate

TIMEOUT_SEC = 10.0
CLOCK_SKEW_LIMIT_SEC = 120


class WorkerError(Exception):
    """A Worker call failed; ``code`` is a ``SecureAddressErrorCode``."""

    def __init__(self, code: str, *, retry_after: int | None = None, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after
        self.status = status


class BearerRejected(WorkerError):
    """``401``: the bearer is invalid (restored or cloned workspace). Re-register."""

    def __init__(self) -> None:
        super().__init__("registration_refused", status=401)


@dataclass(frozen=True)
class RegisterResult:
    household_id: str
    name: str
    bearer: str


@dataclass(frozen=True)
class BudgetResult:
    first_cert_needed: bool
    available: bool
    scope: str | None
    retry_after: int | None


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# Public by design (ADR 115 / ADR 145): this value ships in AGPL source, so it is
# anti-spam friction, not a credential. The Worker's budgets, its RFC 1918 rule and
# its per-address limits are the protection. It is rotated by a Core release (the
# Worker accepts a list of values; see the name service's rotation runbook).
# ``LUMOGIS_HOME_DNS_REGISTRATION_SECRET`` overrides it for a self-hosted name
# service or a maintainer's test value.
DEFAULT_REGISTRATION_SECRET: Final[str] = (
    "a8450a8b61af8ad85c4951ac28057c2c18cb4c3bdc68dad9130329a02b352cb1"
)


def registration_secret() -> str:
    """The value sent to ``/register``: ``LUMOGIS_HOME_DNS_REGISTRATION_SECRET`` when
    it is set and non-blank, else :data:`DEFAULT_REGISTRATION_SECRET`. A public
    anti-spam value, not access control."""
    value = os.environ.get("LUMOGIS_HOME_DNS_REGISTRATION_SECRET", "").strip()
    return value or DEFAULT_REGISTRATION_SECRET


def _retry_after(resp: httpx.Response) -> int | None:
    try:
        return max(0, int(resp.headers.get("Retry-After", "")))
    except ValueError:
        try:
            body = resp.json()
            value = body.get("retry_after_seconds") if isinstance(body, dict) else None
            return int(value) if value is not None else None
        except (ValueError, TypeError):
            return None


def _error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        return str(body.get("error", "")) if isinstance(body, dict) else ""
    except ValueError:
        return ""


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        return httpx.request(
            method, f"{validate.server_url()}{path}", timeout=TIMEOUT_SEC, **kwargs
        )
    except ValueError as exc:  # invalid LUMOGIS_HOME_DNS_URL
        raise WorkerError("worker_unreachable") from exc
    except httpx.HTTPError as exc:
        raise WorkerError("worker_unreachable") from exc


def _auth(bearer: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer}"}


def _common_errors(resp: httpx.Response) -> None:
    if resp.status_code == 401:
        raise BearerRejected()
    if resp.status_code >= 500:
        if _error_code(resp) == "service_full":
            raise WorkerError("service_full", status=resp.status_code)
        raise WorkerError("worker_unreachable", status=resp.status_code)


def register(key: Ed25519PrivateKey, *, now: float | None = None) -> RegisterResult:
    secret = registration_secret()
    if not secret:
        raise WorkerError("registration_refused")
    pubkey = _b64u(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    ts = int(now if now is not None else time.time())
    sig = _b64u(key.sign(f"register|{pubkey}|{ts}".encode("utf-8")))
    resp = _request(
        "POST",
        "/register",
        json={"pubkey": pubkey, "registration_secret": secret, "ts": ts, "sig": sig},
    )
    if resp.status_code == 201:
        try:
            body = resp.json()
            household_id = str(body["household_id"])
            name = str(body["name"])
            bearer = str(body["token"])
        except (ValueError, KeyError, TypeError) as exc:
            raise WorkerError("registration_refused", status=201) from exc
        if (
            not validate.valid_household_id(household_id)
            or name != validate.name_for(household_id)
            or not validate.valid_bearer(bearer)
        ):
            raise WorkerError("registration_refused", status=201)
        return RegisterResult(household_id=household_id, name=name, bearer=bearer)
    if resp.status_code == 400 and _error_code(resp) == "bad_ts":
        try:
            worker_now = parsedate_to_datetime(resp.headers.get("Date", ""))
            skew = abs((worker_now - datetime.fromtimestamp(ts, timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            skew = 0
        raise WorkerError(
            "clock_skew" if skew > CLOCK_SKEW_LIMIT_SEC else "registration_refused", status=400
        )
    if resp.status_code == 410:
        raise WorkerError("registration_retired", status=410)
    if resp.status_code == 429:
        raise WorkerError("registration_rate_limited", retry_after=_retry_after(resp), status=429)
    if resp.status_code >= 500:
        raise WorkerError("worker_unreachable", status=resp.status_code)
    raise WorkerError("registration_refused", status=resp.status_code)


def put_a(bearer: str, ip: str) -> None:
    resp = _request("PUT", "/a", json={"ip": ip}, headers=_auth(bearer))
    if resp.status_code == 200:
        return
    _common_errors(resp)
    if resp.status_code == 400:
        raise WorkerError("lan_address_unknown", status=400)
    raise WorkerError("worker_unreachable", status=resp.status_code)


def budget(bearer: str) -> BudgetResult:
    resp = _request("GET", "/budget", headers=_auth(bearer))
    if resp.status_code == 200:
        try:
            body = resp.json()
            return BudgetResult(
                first_cert_needed=bool(body["first_cert_needed"]),
                available=bool(body["available"]),
                scope=body.get("scope"),
                retry_after=body.get("retry_after_seconds"),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise WorkerError("worker_unreachable", status=200) from exc
    _common_errors(resp)
    raise WorkerError("worker_unreachable", status=resp.status_code)


def deregister(bearer: str) -> None:
    """Remove the A/TXT records. A ``401`` means the bearer is already invalid: done."""
    resp = _request("DELETE", "/register", headers=_auth(bearer))
    if resp.status_code in (204, 401):
        return
    raise WorkerError("worker_unreachable", status=resp.status_code)
