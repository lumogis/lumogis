# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG service `POST /context` endpoint (synchronous chat-path injection).

Per the extraction plan §"KG service routes (new)" + §"routes/context.py":

  - Bearer token auth: same matrix as `/webhook` (see `webhook.check_webhook_auth`).
  - Body: `ContextRequest` (`query`, `user_id`, `max_fragments`).
  - Response: `ContextResponse` (`fragments: list[str]`).
  - Hard 35 ms in-route budget. The 40 ms outer cap lives on the Core
    client side; this route's budget is its share of that round-trip.
  - The actual graph work is done by `graph.query.on_context_building`,
    which is the verbatim copy of the same function Core's plugin
    runs in inprocess mode — guaranteeing parity.

Why not `signal.SIGALRM` for the budget?
  Routes run on uvicorn worker threads where signals don't apply, and
  SIGALRM is not portable to Windows-host Docker setups. Instead the
  budget is enforced inside `on_context_building` via the existing
  `time.monotonic()` guard in `graph/query.py:616`.

Failure handling:
  - 503 if FalkorDB is unreachable (the helper returns silently in
    that case; we only return 503 when `config.get_graph_store()` is
    None or its ping fails).
  - 200 with empty fragments otherwise: a graph that produces no
    matches is a valid outcome, NOT an error.
"""

import logging
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import ValidationError

import config
from models.webhook import ContextRequest, ContextResponse
from routes.webhook import check_webhook_auth

router = APIRouter()
_log = logging.getLogger(__name__)


@router.post("/context")
def post_context(
    body: ContextRequest,
    authorization: str | None = Header(default=None),
) -> ContextResponse:
    """Build a list of `[Graph]` context fragments for `body.query`."""
    check_webhook_auth(authorization)

    gs = config.get_graph_store()
    if gs is None:
        raise HTTPException(status_code=503, detail="graph store unavailable")

    try:
        # Validate the request body (FastAPI already did this, but if a
        # caller hand-crafts the URL with `body=None` somehow we still
        # surface a clean 422 instead of an AttributeError below).
        ContextRequest.model_validate(body.model_dump())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    fragments: list[str] = []
    t0 = time.monotonic()
    try:
        from graph.query import on_context_building

        on_context_building(query=body.query, context_fragments=fragments)
    except Exception:
        _log.exception("/context: on_context_building raised — returning empty fragments")

    duration_ms = int((time.monotonic() - t0) * 1000)
    if duration_ms > 40:
        _log.info(
            "/context: in-route work exceeded 35 ms budget (took %d ms) — "
            "Core client likely already timed out",
            duration_ms,
        )

    capped = fragments[: body.max_fragments] if body.max_fragments else fragments
    return ContextResponse(fragments=capped)
