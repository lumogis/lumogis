# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP dispatch to a capability service's ``POST /tools/<name>`` endpoint.

`CapabilityManifest` does not yet pin a full invoke URL; Core follows the
`lumogis-graph` shape: ``{base}/tools/{tool_name}`` with
``X-Lumogis-User`` (attribution only) and optional ``Authorization: Bearer``
shared secret. Generic callers can require a service bearer; the KG
graph proxy gates missing secrets via
:func:`config.get_graph_proxy_require_service_bearer` (Core operator opt-in
``LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET`` — **not** an automatic
read of the KG service's ``KG_ALLOW_INSECURE_WEBHOOKS``).

X-Lumogis-User is **not** authentication; service auth is the bearer when
configured, or the request is rejected (when :data:`REQUIRE_BEARER_DEFAULT`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

import httpx

_log = logging.getLogger(__name__)

REQUIRE_BEARER_DEFAULT: Final[bool] = True
USER_ATTRIBUTION_HEADER: Final[str] = "X-Lumogis-User"


@dataclass(frozen=True)
class CapabilityHttpToolProxy:
    """Configured bundle for ``POST {base}/tools/{tool_name}``.

    Thin convenience over :func:`post_capability_tool_invocation`; use the
    function directly when you need fully custom headers or tests.
    """

    base_url: str
    tool_name: str
    timeout_s: float
    require_service_bearer: bool = REQUIRE_BEARER_DEFAULT
    unavailable_message: str = "capability: service unavailable"

    def post(
        self,
        *,
        user_id: str,
        body: dict,
        service_bearer: str | None,
    ) -> HttpInvokeResult:
        return post_capability_tool_invocation(
            base_url=self.base_url,
            tool_name=self.tool_name,
            user_id=user_id,
            json_body=body,
            timeout_s=self.timeout_s,
            service_bearer=service_bearer,
            require_service_bearer=self.require_service_bearer,
            unavailable_message=self.unavailable_message,
        )


@dataclass(frozen=True)
class HttpInvokeResult:
    """Structured result of :func:`post_capability_tool_invocation`."""

    ok: bool
    text: str
    http_status: int | None = None
    error_reason: str | None = None


def post_capability_tool_invocation(
    *,
    base_url: str,
    tool_name: str,
    user_id: str,
    json_body: dict,
    timeout_s: float,
    service_bearer: str | None,
    require_service_bearer: bool = REQUIRE_BEARER_DEFAULT,
    unavailable_message: str = "capability: service unavailable",
) -> HttpInvokeResult:
    """POST JSON to ``{base}/tools/{tool_name}`` with user attribution + auth.

    If ``require_service_bearer`` is true and there is no non-empty
    service bearer, returns fail-closed without HTTP (``ok=False``,
    `error_reason=missing_service_auth`).

    On non-200 responses or transport errors, returns ``ok=False`` and the
    same user-facing `unavailable_message` in ``text`` (fail-soft), matching
    the graph proxy's behaviour.
    """
    if require_service_bearer and not (service_bearer and str(service_bearer).strip()):
        return HttpInvokeResult(
            ok=False,
            text=unavailable_message,
            error_reason="missing_service_auth",
        )
    if not require_service_bearer and not (service_bearer and str(service_bearer).strip()):
        service_bearer = None
    if not user_id or not base_url or not tool_name:
        return HttpInvokeResult(
            ok=False,
            text=unavailable_message,
            error_reason="invalid_arguments",
        )
    b = base_url.rstrip("/")
    if not b:
        return HttpInvokeResult(
            ok=False,
            text=unavailable_message,
            error_reason="invalid_base_url",
        )
    url = f"{b}/tools/{tool_name.lstrip('/')}"
    headers: dict[str, str] = {USER_ATTRIBUTION_HEADER: user_id}
    if service_bearer:
        headers["Authorization"] = f"Bearer {service_bearer}"

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s)) as client:
            resp = client.post(url, json=json_body, headers=headers)
    except httpx.HTTPError as exc:
        _log.warning(
            "capability_http: %s POST %s failed (%s)",
            type(exc).__name__,
            url,
            exc,
        )
        return HttpInvokeResult(
            ok=False,
            text=unavailable_message,
            http_status=None,
            error_reason=type(exc).__name__,
        )
    except Exception:
        _log.exception("capability_http: unexpected error POSTing %s", url)
        return HttpInvokeResult(
            ok=False,
            text=unavailable_message,
            error_reason="exception",
        )

    if resp.status_code != 200:
        _log.warning(
            "capability_http: %s returned %d (body=%r)",
            url,
            resp.status_code,
            resp.text[:500],
        )
        return HttpInvokeResult(
            ok=False,
            text=unavailable_message,
            http_status=resp.status_code,
            error_reason=f"http_{resp.status_code}",
        )

    return HttpInvokeResult(ok=True, text=resp.text, http_status=200)
