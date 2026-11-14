# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG service `POST /tools/query_graph` endpoint (capability invoke contract v1).

This is the HTTP transport for the KG service's `graph.query` capability tool
(the manifest declares `invoke.path=/tools/query_graph`). Core's
`register_query_graph_proxy` invokes it when `GRAPH_MODE=service`.

Contract (LUM-41 v1):

  - Bearer token auth (same matrix as `/webhook`) — a transport-level failure
    (401 / 503 "webhook auth not configured") stays a non-200 status; Core maps
    those to `unauthorized` / `unavailable`.
  - Request: the v1 envelope
        {"contract_version": "1.0", "tool": "graph.query",
         "arguments": {"mode","entity","max_depth","user_id"},
         "meta": {"user", "request_id"}}
    The handler reads `arguments` (the same polymorphic shape today's
    `query_graph_tool` expects).
  - Response: the v1 envelope. Success → `{"ok": true, "output": <result>}`.
    Tool-level failures (timeout, budget exceeded, internal) → **HTTP 200** with
    `{"ok": false, "error": {"code", "message", "retryable"}}` so `retryable`
    always survives to Core. The 2 s wall-clock budget is a contract-level
    requirement, not a tunable, in phase 1.

Why a separate `/tools/query_graph` route rather than dispatching through
`/mcp`? Core's ToolSpec dispatch returns plain strings and this is the low
-latency Core ↔ KG fast-path; external MCP clients still get the tool over
`/mcp` via `mcp/server.py`.
"""

import logging
import time

from fastapi import APIRouter
from fastapi import Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic import Field

from routes.webhook import check_webhook_auth

router = APIRouter()
_log = logging.getLogger(__name__)


_QUERY_GRAPH_BUDGET_S = 2.0


class QueryGraphRequest(BaseModel):
    """v1 invoke request envelope for POST /tools/query_graph.

    Permissive by design: `arguments` is a free-form dict because
    `query_graph_tool` validates the per-`mode` shape itself. The envelope
    metadata (`contract_version`, `tool`, `meta`) is accepted but only
    `arguments` drives the query.
    """

    contract_version: str = "1.0"
    tool: str | None = None
    arguments: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)


def _error(code: str, message: str, *, retryable: bool) -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}


@router.post("/tools/query_graph")
def post_query_graph(
    body: QueryGraphRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Invoke the `graph.query` capability tool via `graph.query.query_graph_tool`."""
    check_webhook_auth(authorization)

    from graph.query import query_graph_tool

    # `user_id` is trusted here only because Core's proxy sets it behind the
    # webhook bearer (kg_premium_core.py); Core is this route's only client.
    # The proxy maps the invalid_request envelope to GRAPH_QUERY_UNAVAILABLE;
    # Core always sends user_id, so that path is defensive only (LUM-884).
    args = dict(body.arguments)
    uid = args.pop("user_id", None)
    if not isinstance(uid, str) or not uid:
        _log.warning("/tools/query_graph: missing arguments.user_id")
        return JSONResponse(
            status_code=200,
            content=_error("invalid_request", "query_graph: user_id is required", retryable=False),
        )

    t0 = time.monotonic()
    try:
        output = query_graph_tool(args, user_id=uid)
    except TimeoutError:
        _log.warning(
            "/tools/query_graph: timed out after %.2fs (budget %.2fs)",
            time.monotonic() - t0,
            _QUERY_GRAPH_BUDGET_S,
        )
        return JSONResponse(
            status_code=200,
            content=_error("timeout", "query_graph: graph service unavailable", retryable=True),
        )
    except Exception:
        _log.exception("/tools/query_graph: unexpected error")
        return JSONResponse(
            status_code=200,
            content=_error("internal", "query_graph: internal error", retryable=False),
        )

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if elapsed_ms > int(_QUERY_GRAPH_BUDGET_S * 1000):
        # The handler did not raise but we exceeded the budget; surface a
        # timeout error. query_graph_tool's internal time.monotonic() guard
        # SHOULD have raised TimeoutError above, but we belt-and-brace here in
        # case a future implementation forgets — the contract is the wall-clock
        # cap, not the helper's internal logic.
        _log.warning(
            "/tools/query_graph: returned without raising but exceeded %d ms budget (took %d ms)",
            int(_QUERY_GRAPH_BUDGET_S * 1000),
            elapsed_ms,
        )
        return JSONResponse(
            status_code=200,
            content=_error("timeout", "query_graph: graph service unavailable", retryable=True),
        )

    return JSONResponse(status_code=200, content={"ok": True, "output": output})
