# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/events`` — alias to the shipped SSE stream.

Plan §API routes → Events: one alias under the v1 prefix so the SPA's
codegen surface is uniform. Same ``Last-Event-ID`` semantics, same
user-scoped fanout — we delegate directly to
:func:`routes.events.sse_stream` so the wire behaviour cannot drift.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from authz import require_user
from routes.events import sse_stream

router = APIRouter(prefix="/api/v1", tags=["v1-events"])


@router.get("/events", dependencies=[Depends(require_user)])
async def events_alias(request: Request):
    return await sse_stream(request)
