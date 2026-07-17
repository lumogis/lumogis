# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Non-product sample capability for Phase 5 compose / contract smoke only.

Contract fixture (capability invoke v1 — LUM-41): ``GET /capabilities``,
``GET /health``, ``POST /tools/mock.echo_ping`` with the versioned request/
response envelopes. No database; shared secret via ``MOCK_CAPABILITY_SHARED_SECRET``.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic import Field

SERVICE_ID = "lumogis.mock.echo"
CONTRACT_VERSION = "1.0"
TOOL_NAME = "mock.echo_ping"
INVOKE_PATH = f"/tools/{TOOL_NAME}"

MANIFEST: dict[str, Any] = {
    "name": "Lumogis mock echo capability",
    "id": SERVICE_ID,
    "version": "0.0.1",
    "type": "service",
    "transport": "http",
    "license_mode": "community",
    "maturity": "preview",
    "description": "Dev-only echo tool for second-capability compose smoke (not a product).",
    "contract_version": CONTRACT_VERSION,
    "auth": {
        "mode": "bearer",
        "credential_ref": "MOCK_CAPABILITY_SHARED_SECRET",
    },
    "tools": [
        {
            "name": TOOL_NAME,
            "description": "Echo invoke arguments (requires bearer).",
            "license_mode": "community",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "is_write": False,
            "idempotent": True,
            "invoke": {"method": "POST", "path": INVOKE_PATH},
        }
    ],
    "health_endpoint": "/health",
    "capabilities_endpoint": "/capabilities",
    "permissions_required": [],
    # LUM-618: the single declared egress host for the containment proof. Kept in
    # sync with docker/egress-proxy/allow/lumogis.mock.echo.txt (regenerate that
    # allow file if this changes). The mock makes no product outbound calls; the
    # /debug/egress-probe endpoint below exercises this host for the CI test.
    "external_endpoints": ["example.com"],
    "config_schema": {"type": "object"},
    "min_core_version": "0.3.0rc1",
    "maintainer": "lumogis-dev",
}

# LUM-618 containment test target: the single declared/allowed egress host.
ALLOWED_EGRESS_HOST = "example.com"

app = FastAPI(title="lumogis-mock-capability", version="0.0.1")


class InvokeRequest(BaseModel):
    """v1 invoke request envelope."""

    contract_version: str = CONTRACT_VERSION
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}


def _check_bearer(authorization: str | None) -> None:
    secret = (os.environ.get("MOCK_CAPABILITY_SHARED_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=500, detail="MOCK_CAPABILITY_SHARED_SECRET unset")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.removeprefix("Bearer ").strip()
    if token != secret:
        raise HTTPException(status_code=403, detail="invalid bearer")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/capabilities")
def capabilities() -> dict[str, Any]:
    return MANIFEST


@app.get("/debug/egress-probe")
def egress_probe(host: str) -> JSONResponse:
    """LUM-618 test-only outbound probe (NOT a product tool).

    Attempts an HTTPS GET to ``host`` (from inside this container). Used by the
    containment integration test to assert: the allowed host is reachable
    (spliced through the proxy) and any other host is refused / has no route.
    Reports the outcome so the test can distinguish a proxy-deny from a
    no-route/timeout. Never used in production dispatch.
    """
    import urllib.error
    import urllib.request

    url = f"https://{host}/"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310 (test-only)
            return JSONResponse(
                status_code=200,
                content={"ok": True, "host": host, "status": resp.status},
            )
    except urllib.error.HTTPError as exc:
        # A proxy 403 (TCP_DENIED) surfaces here — the host was refused by Squid.
        return JSONResponse(
            status_code=200,
            content={"ok": False, "host": host, "reason": "http_error", "code": exc.code},
        )
    except Exception as exc:  # noqa: BLE001 (report any failure verbatim for the test)
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "host": host,
                "reason": type(exc).__name__,
                "detail": str(exc)[:200],
            },
        )


@app.post(INVOKE_PATH)
def mock_echo_ping(
    body: InvokeRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> JSONResponse:
    _check_bearer(authorization)
    if body.tool != TOOL_NAME:
        return JSONResponse(
            status_code=200,
            content=_error("not_found", f"unknown tool: {body.tool}", retryable=False),
        )
    return JSONResponse(status_code=200, content={"ok": True, "output": {"echo": body.arguments}})
