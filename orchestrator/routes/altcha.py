# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Public ALTCHA challenge endpoint (LUM-65).

Unauthenticated by necessity — the widget fetches a challenge before the user
has done anything. Rate-limited per IP because minting is cheap for us and the
solving cost lands entirely on the client: without a ceiling this would be a
free way to make the server sign work it will never see again.
"""

from __future__ import annotations

from csrf import _proxied_client_ip
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from rate_limit import RequestRateLimiter

from services import altcha_challenge

router = APIRouter(prefix="/api/v1/altcha", tags=["altcha"])

_challenge_limiter = RequestRateLimiter(max_requests=30, window_seconds=60.0)


def _reset_limiters_for_tests() -> None:
    _challenge_limiter.clear()


@router.get("/challenge")
def get_altcha_challenge(request: Request) -> dict:
    """Mint one proof-of-work challenge for the widget.

    No ``response_model``: the body is the upstream v2 wire shape
    (``{"parameters": {...}, "signature": "..."}``) and it belongs to the
    library, not to us — pinning it in a Pydantic model here would mean
    editing Lumogis every time the widget protocol adds a KDF parameter.
    """
    if not _challenge_limiter.check(_proxied_client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many requests; try again in a minute",
            headers={"Retry-After": "60"},
        )
    try:
        return altcha_challenge.create_challenge()
    except altcha_challenge.AltchaNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "altcha_not_configured"},
        ) from None
