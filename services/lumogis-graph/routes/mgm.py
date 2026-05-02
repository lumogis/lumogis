# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG service `GET /mgm` — operator's graph management page.

Returns the static `static/graph_mgm.html` SPA. The page issues fetches
to two distinct origins:

  - Same-origin (`/kg/*`, `/graph/*`)        → handled by this service.
  - Core-origin (`/review-queue*`,
                 `/entities/*`)              → handled by Core.

To make that switch testable, the page reads `window.LUMOGIS_CORE_BASE_URL`
which is injected by this route via a `<script>` tag prepended to the
HTML before the response is returned. When `LUMOGIS_CORE_BASE_URL` is
unset (the dev default), the variable is `""` (empty string) and the
page treats Core URLs as same-origin, which is correct for any deployment
where Core proxies the KG service under a single hostname.
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

router = APIRouter()
_log = logging.getLogger(__name__)


_STATIC_HTML_PATH = Path(__file__).resolve().parent.parent / "static" / "graph_mgm.html"


def _injected_script() -> str:
    """Build the `<script>` that the page reads on load."""
    base = os.environ.get("LUMOGIS_CORE_BASE_URL", "").strip().rstrip("/")
    return (
        "<script>\n"
        f"  window.LUMOGIS_CORE_BASE_URL = {_js_string(base)};\n"
        "</script>\n"
    )


def _js_string(s: str) -> str:
    """Encode a Python string as a safe JavaScript string literal."""
    escaped = (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("</", "<\\/")
    )
    return f'"{escaped}"'


@router.get("/mgm")
def get_mgm() -> Response:
    """Serve the graph management page with the Core-base-URL `<script>` injected."""
    if not _STATIC_HTML_PATH.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"graph management page not found at {_STATIC_HTML_PATH}",
        )

    try:
        html = _STATIC_HTML_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"could not read graph management page: {exc}",
        ) from exc

    script = _injected_script()
    if "<head>" in html:
        html = html.replace("<head>", "<head>\n" + script, 1)
    elif "<html>" in html:
        html = html.replace("<html>", "<html>\n" + script, 1)
    else:
        html = script + html

    return Response(content=html, media_type="text/html")
