# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""First Lumogis Web slice — serves the static SPA shell.

Single responsibility: hand back the static HTML at ``/web`` (and ``/web/``).
The SPA itself is a single self-contained file with no build step and no
external dependencies; it consumes ``/api/v1/auth/*`` directly via fetch.

This route is **deliberately unauthenticated** — it has to be reachable so
the user can see the login form. The page body contains no secrets; all
authenticated work happens in the browser via subsequent fetch calls that
attach the bearer the user obtains by signing in. ``/web`` is therefore
listed in :data:`auth._AUTH_BYPASS_PREFIXES` so the bearer gate doesn't
short-circuit it when ``AUTH_ENABLED=true``.

Why a single static HTML file (and not React/Vite)
--------------------------------------------------
The family-LAN plan's §24 calls for a *first slice* that proves the
``/api/v1/auth/*`` foundations are usable end-to-end by a real browser
client. A build pipeline at this stage would be disproportionate to the
success criteria (login → see email + role → call one authenticated
endpoint → logout). When the slice grows beyond what one HTML file can
carry, swap this route for a ``StaticFiles`` mount of a built bundle.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

_log = logging.getLogger(__name__)

_WEB_HTML = Path(__file__).parent.parent / "web" / "index.html"

router = APIRouter(tags=["web"])


@router.get("/web", include_in_schema=False)
def web_root_redirect() -> RedirectResponse:
    """Redirect ``/web`` → ``/web/`` so relative URLs resolve correctly.

    Same pattern Starlette uses for directory mounts. Without this, a user
    typing ``http://host:8000/web`` would get the page but any future
    relative asset path would break.
    """
    return RedirectResponse(url="/web/", status_code=307)


@router.get("/web/", include_in_schema=False)
def web_index() -> FileResponse:
    """Serve the Lumogis Web SPA shell."""
    if not _WEB_HTML.exists():
        raise HTTPException(
            status_code=500,
            detail="Web shell not found. Check that orchestrator/web/index.html exists.",
        )
    return FileResponse(_WEB_HTML, media_type="text/html")
