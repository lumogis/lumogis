# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG service `POST /tools/query_graph` proxy endpoint.

This is the HTTP transport for the `query_graph` ToolSpec that Core's
`register_query_graph_proxy` registers when `GRAPH_MODE=service`. The
contract (per plan §"KG service routes (new)"):

  - Bearer token auth (same matrix as `/webhook`).
  - Body: `{"input": dict}` — the `input` dict is the same shape today's
    `query_graph_tool` expects:
        {"mode": "ego" | "path" | "mentions",
         "entity": str,
         "max_depth": int (≤ 4),
         "user_id": str}
  - Response: `{"output": str}` on 200.
  - 401 on bad bearer.
  - 422 on bad input (Pydantic validation).
  - 504 if the underlying Cypher exceeds 2 s wall-clock (the per-query
    timeout enforced inside `graph.query.query_graph_tool` via the
    existing `time.monotonic()` guard pattern). The 2 s budget is a
    contract-level requirement, not a tunable, in phase 1.

Why a separate `/tools/query_graph` route rather than dispatching through
`/mcp`?
  - Core's `services/tools.py` already has a ToolSpec dispatch contract
    that returns plain strings; mapping that onto MCP's streamable
    response format is a re-implementation of `query_graph_tool` for no
    user benefit.
  - Lower latency: the MCP transport adds a JSON-RPC envelope on top of
    HTTP, doubling the per-call serialisation work.
  - External MCP clients (Thunderbolt) still get the tool over `/mcp`
    via `mcp/server.py`. This route is purely the Core ↔ KG
    fast-path.
"""

import logging
import time

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from routes.webhook import check_webhook_auth

router = APIRouter()
_log = logging.getLogger(__name__)


_QUERY_GRAPH_BUDGET_S = 2.0


class QueryGraphRequest(BaseModel):
    """Wire format of POST /tools/query_graph.

    `input` is intentionally a free-form dict because `query_graph_tool`
    accepts a polymorphic shape per `mode`. We let the dispatcher do
    the per-mode validation; here we only enforce that `input` is an
    object and not (e.g.) a string.
    """

    input: dict = Field(default_factory=dict)


@router.post("/tools/query_graph")
def post_query_graph(
    body: QueryGraphRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Proxy a `query_graph` tool invocation to `graph.query.query_graph_tool`."""
    check_webhook_auth(authorization)

    try:
        QueryGraphRequest.model_validate(body.model_dump())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    from graph.query import query_graph_tool

    t0 = time.monotonic()
    try:
        output = query_graph_tool(body.input)
    except TimeoutError as exc:
        _log.warning(
            "/tools/query_graph: timed out after %.2fs (budget %.2fs)",
            time.monotonic() - t0,
            _QUERY_GRAPH_BUDGET_S,
        )
        return JSONResponse(
            status_code=504,
            content={
                "detail": "query_graph: graph service unavailable",
                "reason": "timeout",
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            },
        )
    except HTTPException:
        raise
    except Exception:
        _log.exception("/tools/query_graph: unexpected error")
        raise HTTPException(status_code=500, detail="query_graph: internal error") from None

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if elapsed_ms > int(_QUERY_GRAPH_BUDGET_S * 1000):
        # The handler did not raise but we exceeded the budget; surface 504.
        # query_graph_tool's internal time.monotonic() guard SHOULD have
        # raised TimeoutError above, but we belt-and-brace at this layer
        # in case a future implementation forgets — the contract is the
        # wall-clock cap, not the helper's internal logic.
        _log.warning(
            "/tools/query_graph: returned without raising but exceeded %d ms budget (took %d ms)",
            int(_QUERY_GRAPH_BUDGET_S * 1000),
            elapsed_ms,
        )
        return JSONResponse(
            status_code=504,
            content={
                "detail": "query_graph: graph service unavailable",
                "reason": "budget_exceeded",
                "elapsed_ms": elapsed_ms,
            },
        )

    return JSONResponse(status_code=200, content={"output": output})
