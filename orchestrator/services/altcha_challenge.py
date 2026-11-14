# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Self-hosted ALTCHA proof-of-work for the public reset request (LUM-65).

Two things about the upstream library are load-bearing enough to state here,
because getting either wrong fails **open** — the CAPTCHA appears to work and
admits everything:

1. **Branch.** ``altcha==2.1.0``'s top-level ``create_challenge`` /
   ``verify_solution`` are the **v2** protocol: ``create_challenge(algorithm,
   cost, *, hmac_secret=, expires_at=)``. The v1 API (``max_number``,
   ``hmac_key``, ``expires``) still exists as ``create_challenge_v1`` /
   ``verify_solution_v1``. The npm widget is pinned to the matching v2 major.

2. **Result type.** ``verify_solution`` returns a ``VerifySolutionResult``
   object with **no** ``__bool__`` — so ``if not verify_solution(...)`` is
   always False and every solution passes. (The v1 function returns a
   ``tuple[bool, str | None]``, which is truthy for the same reason.) This
   module returns a plain ``bool`` read from ``.verified`` so no caller can
   make that mistake; ``test_altcha.py`` fails if the check regresses.

Verification is also **stateless** upstream: a solved payload stays valid for
the whole challenge window, so one proof of work would otherwise fund every
request the rate limiter allows. :func:`verify` consumes the challenge
signature in a single-use store to close that.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

_log = logging.getLogger(__name__)

# ALTCHA **v2** ``cost``: the per-candidate KDF iteration count. It is *not*
# v1's ``max_number`` search space, and the difference is why this was wrong.
# With ``keyPrefix "00"`` the solver tries ~256 candidates on average, so total
# work is ``cost × ~256`` — carrying v1's 50_000 across made a challenge that
# measured **unsolved after 231s** against the widget's 90s default timeout.
# The page rendered "Verification could not be completed" and the feature was
# unusable at its shipped default.
#
# Measured on this repo's `clients/lumogis-web/_bench.mjs` (median of 7, real
# solver, idle machine): 100→1.1s, 200→1.4s, 400→2.8s, 800→6.3s, 1600→10.0s.
# Median tracks ``cost × ~7ms``; the solve is a geometric search, so the mean is
# ~1.4× the median and the tail is long — roughly 2% of solves take 4× the mean.
#
# 200 balances three things. A mid-range phone is ~5× slower than the bench
# machine, putting a typical solve near 7s and the 2% tail near 30s, safely
# inside the 90s timeout that `altcha.ts` does not override. The two
# `RequestRateLimiter` ceilings on the request route (10/min per IP, 5/min per
# address) are the actual anti-abuse control; this proof of work is
# defence-in-depth against trivial scripted abuse, not the wall. And the test
# suite already pins `LUMOGIS_ALTCHA_COST=200`, so the suite now exercises the
# value the product ships — the divergence that let every test pass while the
# feature was unusable.
#
# Operators can tune via ``LUMOGIS_ALTCHA_COST``.
DEFAULT_COST = 200

CHALLENGE_TTL_SECONDS = 300

ALGORITHM = "SHA-256"


class AltchaNotConfigured(Exception):
    """``LUMOGIS_ALTCHA_HMAC_KEY`` is unset — fail closed, never invent one."""


def hmac_secret() -> str:
    """The configured HMAC key, or ``""``.

    Deliberately has no default and no first-boot random fallback: an
    ephemeral per-process key would make challenges minted by one worker
    unverifiable by another, which looks like a flaky CAPTCHA rather than a
    misconfiguration, and it would silently reset on every restart.
    """
    return os.environ.get("LUMOGIS_ALTCHA_HMAC_KEY", "").strip()


def cost() -> int:
    raw = os.environ.get("LUMOGIS_ALTCHA_COST", "").strip()
    if not raw:
        return DEFAULT_COST
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_COST


class _UsedSignatures:
    """In-process single-use store for solved challenge signatures.

    Bounded by expiry, not by count: entries are dropped once they pass the
    challenge TTL, at which point the underlying solution has expired anyway.
    In-process is the right scope for a household deployment — it matches
    where ``RequestRateLimiter`` already keeps its state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def consume(self, signature: str) -> bool:
        """Record ``signature``; return False if it was already used."""
        now = time.monotonic()
        with self._lock:
            for key, expires in list(self._seen.items()):
                if expires <= now:
                    del self._seen[key]
            if signature in self._seen:
                return False
            self._seen[signature] = now + CHALLENGE_TTL_SECONDS
            return True

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


_used = _UsedSignatures()


def reset_for_tests() -> None:
    _used.clear()


def create_challenge() -> dict[str, Any]:
    """Mint a challenge as JSON for the widget.

    Raises :class:`AltchaNotConfigured` when no HMAC key is set.
    """
    secret = hmac_secret()
    if not secret:
        raise AltchaNotConfigured("LUMOGIS_ALTCHA_HMAC_KEY is not set")

    import altcha

    challenge = altcha.create_challenge(
        ALGORITHM,
        cost(),
        hmac_secret=secret,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=CHALLENGE_TTL_SECONDS),
    )
    # ``to_dict()`` is the v2 wire shape the widget expects:
    # ``{"parameters": {algorithm, cost, keyLength, keyPrefix, nonce, salt,
    # expiresAt}, "signature": "..."}``. Emitting hand-built v1 fields
    # (``challenge``/``salt``/``maxnumber`` at the top level) would produce a
    # challenge no v2 widget can solve.
    return challenge.to_dict()


def verify(payload: str) -> bool:
    """Return True iff ``payload`` is a valid, unexpired, **unused** solution.

    Raises :class:`AltchaNotConfigured` when no HMAC key is set, so an operator
    error can never be mistaken for a passing check.
    """
    secret = hmac_secret()
    if not secret:
        raise AltchaNotConfigured("LUMOGIS_ALTCHA_HMAC_KEY is not set")
    if not payload or not isinstance(payload, str):
        return False

    import altcha

    try:
        result = altcha.verify_solution(payload, secret)
    except Exception as exc:  # noqa: BLE001 - malformed payloads are just failures
        _log.info("altcha: solution rejected (%s)", type(exc).__name__)
        return False

    # `.verified` explicitly — the result object is always truthy. See module
    # docstring; `test_altcha_bad_solution_rejected` exists to catch a
    # regression to `if not verify_solution(...)`.
    if not getattr(result, "verified", False):
        return False

    signature = _signature_of(payload)
    if signature is None:
        return False
    if not _used.consume(signature):
        _log.info("altcha: replayed solution rejected")
        return False
    return True


def _signature_of(payload: str) -> str | None:
    """Pull the challenge signature out of a solved payload, for single-use.

    A v2 payload is base64 of ``{"challenge": {..., "signature": "..."},
    "solution": {...}}`` — the signature is nested under ``challenge``, not at
    the top level. It is an HMAC over the challenge parameters, so it uniquely
    identifies the challenge this solution answers.
    """
    import base64
    import json

    try:
        decoded = json.loads(base64.b64decode(payload).decode("utf-8"))
    except Exception:  # noqa: BLE001 - not base64-JSON; try raw JSON below
        try:
            decoded = json.loads(payload)
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(decoded, dict):
        return None
    challenge = decoded.get("challenge")
    signature = challenge.get("signature") if isinstance(challenge, dict) else None
    return str(signature) if signature else None
