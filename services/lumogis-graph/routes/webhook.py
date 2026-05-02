# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG service `POST /webhook` endpoint.

Per the extraction plan §"KG service routes (new)":

  - Bearer token auth governed by `GRAPH_WEBHOOK_SECRET`.
    - Secret unset + `KG_ALLOW_INSECURE_WEBHOOKS=false` → 503.
    - Secret unset + opt-in `true`                       → 202 (no auth).
    - Secret set + bad/missing bearer                    → 401.
    - Secret set + correct bearer                        → 202.
  - Body MUST be a `WebhookEnvelope`. The envelope's `payload` field is a
    raw dict; we re-validate it against the class chosen via
    `_PAYLOAD_BY_EVENT[envelope.event]` BEFORE enqueuing.
  - `schema_version` MUST be in `SUPPORTED_SCHEMA_VERSIONS` (currently
    `[1]`); otherwise 422 with a structured `detail` body so Core's
    dispatcher can log a useful message.
  - Successful enqueue → 202 + `{"status": "accepted"}`. The actual
    projection runs on a background `webhook_queue` worker.

The handler maps `WebhookEvent` values directly onto
`graph.writer.on_<event_value>` functions (the enum values are the handler
names, by design — see `models/webhook.py`).
"""

import hmac
import logging
from typing import Callable

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import config
import webhook_queue
from models.webhook import (
    SUPPORTED_SCHEMA_VERSIONS,
    WebhookEnvelope,
    WebhookEvent,
    _PAYLOAD_BY_EVENT,
)

router = APIRouter()
_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth gate (shared with routes/context.py)
# ---------------------------------------------------------------------------


def check_webhook_auth(authorization: str | None) -> None:
    """Enforce the four-cell auth matrix from the plan.

    Raises HTTPException with the right status code and detail. Used by
    BOTH `/webhook` and `/context` so the matrix has exactly one
    implementation.
    """
    expected = config.get_kg_webhook_secret()
    if expected is None:
        if config.kg_allow_insecure_webhooks():
            return
        raise HTTPException(
            status_code=503,
            detail="webhook auth not configured",
        )
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="invalid bearer token")


# ---------------------------------------------------------------------------
# Event → writer-handler mapping
# ---------------------------------------------------------------------------


def _resolve_handler(event: WebhookEvent) -> Callable:
    """Resolve a WebhookEvent to its `graph.writer.on_*` callable.

    Imported lazily so a unit test that doesn't actually project anything
    can mock `webhook_queue.submit` and observe the dispatch decision
    without having `graph.writer` import psycopg2 first.
    """
    from graph import writer

    handler = getattr(writer, event.value, None)
    if handler is None or not callable(handler):
        raise HTTPException(
            status_code=500,
            detail=f"no graph.writer.{event.value} handler is registered",
        )
    return handler


# ---------------------------------------------------------------------------
# POST /webhook
# ---------------------------------------------------------------------------


@router.post("/webhook", status_code=202)
async def post_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Validate envelope + payload, enqueue projection, return 202."""
    check_webhook_auth(authorization)

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type and content_type != "application/json":
        raise HTTPException(
            status_code=415,
            detail=f"Content-Type must be application/json (got {content_type!r})",
        )

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid JSON body: {exc}") from exc

    try:
        envelope = WebhookEnvelope.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    if envelope.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "unsupported schema_version",
                "received": envelope.schema_version,
                "supported": list(SUPPORTED_SCHEMA_VERSIONS),
            },
        )

    payload_cls = _PAYLOAD_BY_EVENT.get(envelope.event)
    if payload_cls is None:
        raise HTTPException(
            status_code=422,
            detail=f"no payload model registered for event={envelope.event.value!r}",
        )

    try:
        payload = payload_cls.model_validate(envelope.payload)
    except ValidationError as exc:
        _log.warning(
            "webhook: payload validation failed event=%s errors=%s",
            envelope.event.value,
            exc.errors(),
        )
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    handler = _resolve_handler(envelope.event)
    payload_kwargs = payload.model_dump()
    try:
        webhook_queue.submit(handler, **payload_kwargs)
    except Exception:
        _log.exception("webhook: failed to enqueue handler for event=%s", envelope.event.value)
        raise HTTPException(status_code=500, detail="failed to enqueue webhook") from None

    return JSONResponse(status_code=202, content={"status": "accepted"})
