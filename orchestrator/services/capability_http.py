# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP dispatch for the capability invoke contract v1 (LUM-41).

Core POSTs a **versioned request envelope** to a tool's declared invoke path
(``base_url + invoke_path``, default ``/tools/{tool_name}``) and parses a
**versioned response envelope** back:

  request  = {"contract_version": "1.0", "tool": ..., "arguments": {...},
              "meta": {"user": <attribution>, "request_id": <uuid>}}
  response = {"ok": true, "output": <any>} | {"ok": false, "error": {...}}

Core parses the response body as an envelope **on every HTTP status** — a valid
``{ok:false,error}`` is surfaced verbatim regardless of status (this is what
finally captures a capability's structured 504/500/422 errors instead of
discarding them). Only a body that is not a parseable v1 envelope falls back to
status-code mapping. On ``ok:true`` (status 200) the ``output`` is validated
against the tool's ``output_schema`` when the schema is non-trivial.

``X-Lumogis-User`` is attribution only, never authentication; service auth is the
bearer (fail-closed when :data:`REQUIRE_BEARER_DEFAULT` and none configured).
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any
from typing import Final

import httpx
from models.capability_invoke import CapabilityInvokeMeta
from models.capability_invoke import CapabilityInvokeRequest
from models.capability_invoke import CapabilityInvokeResponse
from services.capability_output_validator import OutputSchemaError
from services.capability_output_validator import validate_output

_log = logging.getLogger(__name__)

REQUIRE_BEARER_DEFAULT: Final[bool] = True
USER_ATTRIBUTION_HEADER: Final[str] = "X-Lumogis-User"

# Dispatch-layer cap on the raw invoke response, applied unconditionally before
# parsing (a trivial-schema tool skips output validation but must not skip this).
# Tool output flows into LLM context, so 1 MiB is already generous.
INVOKE_OUTPUT_MAX_BYTES: Final[int] = int(
    os.environ.get("LUMOGIS_INVOKE_OUTPUT_MAX_BYTES", str(1 * 1024 * 1024))
)

# Non-envelope non-200 bodies map to a structured code by HTTP status.
_STATUS_ERROR_MAP: Final[dict[int, tuple[str, bool]]] = {
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    503: ("unavailable", True),
    504: ("timeout", True),
}


@dataclass(frozen=True)
class HttpInvokeResult:
    """Structured result of :func:`post_capability_tool_invocation`.

    On success ``ok`` is True and ``output`` holds the parsed JSON ``output``
    (any type, including ``None``). On failure ``ok`` is False and
    ``error_code`` / ``error_message`` / ``retryable`` describe it.
    """

    ok: bool
    output: Any = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    http_status: int | None = None

    @property
    def output_text(self) -> str:
        """The output as a string suitable for an LLM tool result."""
        if isinstance(self.output, str):
            return self.output
        import json

        return json.dumps(self.output)


@dataclass(frozen=True)
class CapabilityHttpToolProxy:
    """Configured bundle for invoking one tool over the v1 contract.

    Thin convenience over :func:`post_capability_tool_invocation`; use the
    function directly when you need fully custom headers or tests.
    """

    base_url: str
    tool_name: str
    timeout_s: float
    invoke_path: str | None = None
    output_schema: dict[str, Any] | None = None
    require_service_bearer: bool = REQUIRE_BEARER_DEFAULT
    unavailable_message: str = "capability: service unavailable"

    def post(
        self,
        *,
        user_id: str,
        arguments: dict,
        service_bearer: str | None,
        request_id: str | None = None,
    ) -> HttpInvokeResult:
        return post_capability_tool_invocation(
            base_url=self.base_url,
            tool_name=self.tool_name,
            user_id=user_id,
            arguments=arguments,
            timeout_s=self.timeout_s,
            service_bearer=service_bearer,
            invoke_path=self.invoke_path,
            output_schema=self.output_schema,
            request_id=request_id,
            require_service_bearer=self.require_service_bearer,
            unavailable_message=self.unavailable_message,
        )


def _fail(
    *,
    code: str,
    message: str,
    retryable: bool = False,
    http_status: int | None = None,
) -> HttpInvokeResult:
    return HttpInvokeResult(
        ok=False,
        error_code=code,
        error_message=message,
        retryable=retryable,
        http_status=http_status,
    )


def post_capability_tool_invocation(
    *,
    base_url: str,
    tool_name: str,
    user_id: str,
    arguments: dict,
    timeout_s: float,
    service_bearer: str | None,
    invoke_path: str | None = None,
    invoke_method: str = "POST",
    request_id: str | None = None,
    output_schema: dict[str, Any] | None = None,
    require_service_bearer: bool = REQUIRE_BEARER_DEFAULT,
    unavailable_message: str = "capability: service unavailable",
) -> HttpInvokeResult:
    """POST the v1 request envelope to a capability tool and parse the response.

    Fail-closed (no HTTP) when ``require_service_bearer`` and no bearer is
    configured (``error_code="missing_service_auth"``). Otherwise returns a
    structured :class:`HttpInvokeResult`; transport/timeout/non-200 failures map
    into the same error vocabulary, and a capability's own ``{ok:false,error}``
    envelope is surfaced verbatim on any status.
    """
    if require_service_bearer and not (service_bearer and str(service_bearer).strip()):
        return _fail(code="missing_service_auth", message=unavailable_message)
    if not require_service_bearer and not (service_bearer and str(service_bearer).strip()):
        service_bearer = None
    if not user_id or not base_url or not tool_name:
        return _fail(code="invalid_arguments", message=unavailable_message)
    b = base_url.rstrip("/")
    if not b:
        return _fail(code="invalid_arguments", message=unavailable_message)

    path = invoke_path or f"/tools/{tool_name.lstrip('/')}"
    if not path.startswith("/"):
        path = "/" + path
    url = b + path

    envelope = CapabilityInvokeRequest(
        tool=tool_name,
        arguments=dict(arguments),
        meta=CapabilityInvokeMeta(
            user=user_id,
            request_id=request_id or str(uuid.uuid4()),
        ),
    )
    headers: dict[str, str] = {USER_ATTRIBUTION_HEADER: user_id}
    if service_bearer:
        headers["Authorization"] = f"Bearer {service_bearer}"

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s)) as client:
            resp = client.request(
                invoke_method,
                url,
                json=envelope.model_dump(mode="json"),
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        _log.warning("capability_http: %s POST %s timed out (%s)", type(exc).__name__, url, exc)
        return _fail(code="timeout", message=unavailable_message, retryable=True)
    except httpx.HTTPError as exc:
        _log.warning("capability_http: %s POST %s failed (%s)", type(exc).__name__, url, exc)
        return _fail(code="unavailable", message=unavailable_message, retryable=True)
    except Exception:
        _log.exception("capability_http: unexpected error POSTing %s", url)
        return _fail(code="internal", message=unavailable_message)

    status = resp.status_code
    body = resp.content or b""

    # Unconditional output byte cap (before parsing) — a trivial-schema tool
    # must not be able to return an unbounded blob into LLM context.
    if len(body) > INVOKE_OUTPUT_MAX_BYTES:
        _log.warning(
            "capability_http: %s returned %d bytes > cap %d — rejecting",
            url,
            len(body),
            INVOKE_OUTPUT_MAX_BYTES,
        )
        return _fail(code="invalid_output", message=unavailable_message, http_status=status)

    if status != 200:
        _log.warning("capability_http: %s returned %d (body=%r)", url, status, body[:500])

    # Parse the body as a v1 envelope on EVERY status. A valid {ok:false,error}
    # is surfaced verbatim regardless of status (captures structured 504/500);
    # {ok:true} is only honoured on 200.
    parsed: CapabilityInvokeResponse | None = None
    try:
        parsed = CapabilityInvokeResponse.model_validate_json(body)
    except ValueError:
        parsed = None

    if parsed is not None and parsed.ok is False and parsed.error is not None:
        return HttpInvokeResult(
            ok=False,
            error_code=parsed.error.code.value,
            error_message=parsed.error.message,
            retryable=parsed.error.retryable,
            http_status=status,
        )

    if parsed is not None and parsed.ok is True and status == 200:
        try:
            validate_output(parsed.output, output_schema)
        except OutputSchemaError as exc:
            _log.warning("capability_http: %s output failed schema: %s", url, exc)
            return _fail(code="invalid_output", message=str(exc), http_status=status)
        return HttpInvokeResult(ok=True, output=parsed.output, http_status=status)

    # Not a usable v1 envelope → fall back to status-code mapping.
    if status == 200:
        # Hard cut: a 200 that is not a v1 envelope is a non-conforming service.
        return _fail(
            code="internal",
            message=unavailable_message,
            http_status=status,
        )
    code, retryable = _STATUS_ERROR_MAP.get(status, ("internal", False))
    return _fail(code=code, message=unavailable_message, retryable=retryable, http_status=status)
