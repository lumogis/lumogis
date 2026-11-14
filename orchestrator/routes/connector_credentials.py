# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP routes for the per-user connector credentials surface.

Two routers (mirror :mod:`routes.mcp_tokens`):

* ``router``       — user-facing ``/api/v1/me/connector-credentials``,
                     gated by :func:`authz.require_user`.
* ``admin_router`` — admin-only
                     ``/api/v1/admin/users/{user_id}/connector-credentials``,
                     gated by :func:`authz.require_admin`.

Both routers attach :func:`csrf.require_same_origin` to every mutating
verb (per plan §6 + §API routes). Bearer-authenticated calls bypass
the check by design (see ``orchestrator/csrf.py``), so curl + the
dashboard's ``fetch`` flows keep working unchanged.

Domain → HTTP mapping (D6a, in-scope)
-------------------------------------
A single :func:`_to_http` helper converts the three domain exceptions
raised by :mod:`services.connector_credentials` into the HTTP shapes
documented in the plan §API routes table:

* :class:`UnknownConnector`        → ``422 unknown_connector``      (PUT only)
* :class:`ConnectorNotConfigured`  → ``404 connector_not_configured`` (delegated to the
  GET-single / DELETE / future-runtime caller; this module's GET-single
  + DELETE handlers raise it directly when the service returns ``None``
  / ``False`` rather than catching a service-raised exception)
* :class:`CredentialUnavailable`   → ``503 credential_unavailable``
* ``ValueError`` from ``validate_format`` → ``400 bad_connector_id``

``UnknownConnector`` and ``ConnectorNotConfigured`` are deliberately
**not** caught for GET-single + DELETE (per the registry-strictness
model adopted in R2): those service paths are format-strict only, so
the connector being absent from the canonical
:data:`connectors.registry.CONNECTORS` mapping does not make the row
inaccessible. Stale-row management lives here.

Information-leak guard (parity with mcp_tokens)
-----------------------------------------------
The user-facing GET-single and DELETE handlers do **not** need a
cross-user guard because the route's path uses the caller's own
``user_id`` from the auth context — there is no path-level
``user_id`` to mis-target. The admin router's "unknown user_id"
case is mapped to ``404`` rather than ``403`` for the same reason
the ``mcp_tokens`` admin DELETE does so: returning ``403`` here
would let a non-admin probe user-id existence (though admin-gating
already prevents non-admins from reaching this branch).

Audit emission
--------------
Lifecycle audits (``__connector_credential__.put`` /
``.deleted``) are emitted **inside** the service module
(:func:`services.connector_credentials._emit_audit`); routes do not
re-emit. This keeps the audit-row contract tied to the data
mutation it describes — if the SQL succeeds and the audit fails,
the failure is logged but not re-raised (per the service contract).
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from auth import get_user
from authz import require_admin
from authz import require_user
from connectors.registry import OPERATOR_MANAGED_CONNECTORS
from connectors.registry import UnknownConnector
from connectors.registry import is_multi_instance
from connectors.registry import is_registered
from connectors.registry import iter_registered_with_descriptions
from connectors.registry import secret_field_names
from connectors.registry import validate_format
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Response
from fastapi import status
from models.connector_credential import ConnectorCredentialList
from models.connector_credential import ConnectorCredentialPublic
from models.connector_credential import ConnectorTestResponse
from models.connector_credential import HouseholdConnectorCredentialList
from models.connector_credential import HouseholdConnectorCredentialPublic
from models.connector_credential import InstanceSystemConnectorCredentialList
from models.connector_credential import InstanceSystemConnectorCredentialPublic
from models.connector_credential import PatchConnectorCredentialRequest
from models.connector_credential import PutConnectorCredentialRequest
from rate_limit import RequestRateLimiter
from services.email_credentials import ImapHostMismatchError
from services.email_credentials import ImapSourceNotFoundError
from services.email_credentials import ImapSourceUrlInvalidError
from services.email_credentials import validate_imap_instance_payload_against_source
from services.llm_connector_map import LLM_CONNECTOR_BY_ENV

from services import connector_credentials as ccs
from services import connector_probes as probes
from services import credential_tiers as cts
from services import users as users_service

_log = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/me/connector-credentials",
    tags=["me-connector-credentials"],
    dependencies=[Depends(require_user)],
)

admin_router = APIRouter(
    prefix="/api/v1/admin/users/{user_id}/connector-credentials",
    tags=["admin-connector-credentials"],
    dependencies=[Depends(require_admin)],
)

# Household + instance/system credential tier routers (per ADR
# ``credential_scopes_shared_system``). Both are admin-only and
# admin-only-visible — no per-user route reveals that household /
# system credentials exist (privacy posture from the ADR).
household_admin_router = APIRouter(
    prefix="/api/v1/admin/connector-credentials/household",
    tags=["admin-connector-credentials-household"],
    dependencies=[Depends(require_admin)],
)
system_admin_router = APIRouter(
    prefix="/api/v1/admin/connector-credentials/system",
    tags=["admin-connector-credentials-system"],
    dependencies=[Depends(require_admin)],
)

# LUM-834 — test-connection is an authenticated primitive that makes an outbound
# request to a user-supplied address, the same shape ADR-216 guarded for
# RSS/page. 10/60s per user, mirroring `_email_test_limiter`.
#
# It is also the ONLY guard against stacking: there is no concurrency control,
# so ten clicks in a minute is ten in-flight probes each holding a ~5s budget.
_connector_test_limiter = RequestRateLimiter(max_requests=10, window_seconds=60.0)


# ---------------------------------------------------------------------------
# Projections + error mapping — module-private so route bodies stay flat and
# the never-leak invariants (no ciphertext, no plaintext) live in one place.
# ---------------------------------------------------------------------------


def _to_public(record: ccs.CredentialRecord) -> ConnectorCredentialPublic:
    """Project a service ``CredentialRecord`` onto the wire model.

    ``delivery_paused_detail`` is persisted for operators only — it never
    appears on connector-credential GET responses.

    **Two LUM-834 bridges live here and both are load-bearing.** The record
    calls the column ``configured_keys``; the wire calls it
    ``configured_fields``. Leaving the record's name in the dict is an
    ``extra="forbid"`` ValidationError, so forgetting the rename 500s every
    credential route — loud, and the suite catches it.

    ``secret_fields`` is the quiet one. It is not on the record and cannot be
    (secrecy belongs to the connector, not to one user's row), so it is
    injected from the registry here. Its wire default is ``[]``, which means
    forgetting the injection does NOT fail validation: every response would
    simply claim the connector has no secret fields, and the card would render
    values that should be masked as ordinary rows. Pinned by
    ``test_secret_fields_comes_from_the_registry_not_the_row``.
    """
    data = asdict(record)
    data.pop("delivery_paused_detail", None)
    data["configured_fields"] = data.pop("configured_keys", None)
    data["secret_fields"] = secret_field_names(record.connector)
    return ConnectorCredentialPublic.model_validate(data)


def _bad_connector_id_400(connector: str, exc: ValueError) -> HTTPException:
    """400 body shape for format-validation failures.

    The exception's ``str(exc)`` already names the failure cause
    ("connector exceeds max length 64", "connector must match …",
    etc.) — included verbatim so the client can correct its input
    without a second round trip.
    """
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "bad_connector_id",
            "connector": connector,
            "message": str(exc),
        },
    )


def _unknown_connector_422(connector: str) -> HTTPException:
    """422 body shape when PUT targets an id that's not in the registry."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "unknown_connector",
            "connector": connector,
        },
    )


def _instance_error_422(connector: str, exc: Exception) -> HTTPException:
    """422 when the (connector, instance) pairing violates the multi-instance rule.

    LUM-170: a non-empty instance on a single-instance connector, or an empty
    instance on a multi-instance connector (imap) write.
    """
    code = (
        "instance_required"
        if "requires" in str(exc) or "required" in str(exc)
        else "instance_not_allowed"
    )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": code, "connector": connector, "message": str(exc)},
    )


def _unknown_connector_404(connector: str) -> HTTPException:
    """404 for a test against an id that is not in the registry (LUM-834).

    Deliberately **not** the ``422 unknown_connector`` that PUT returns. PUT's
    422 is a validation verdict on a body the caller supplied; ``/test`` has no
    body, so an unregistered id means the *resource* does not exist. Same
    ``code`` vocabulary, different status, matching the shape of every other
    "nothing here" answer on this surface.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "unknown_connector",
            "connector": connector,
        },
    )


def _connector_not_configured_404(connector: str) -> HTTPException:
    """404 body shape — the canonical missing-row response."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "connector_not_configured",
            "connector": connector,
        },
    )


def _imap_host_mismatch_422() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "imap_host_mismatch",
            "connector": "imap",
            "message": "IMAP host or port does not match the mailbox source URL",
        },
    )


def _imap_source_not_found_422() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "imap_source_not_found",
            "connector": "imap",
            "message": "No IMAP mailbox source exists for this instance id",
        },
    )


def _imap_source_url_invalid_422() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "imap_source_url_invalid",
            "connector": "imap",
            "message": "Mailbox source URL is malformed",
        },
    )


# ---------------------------------------------------------------------------
# Per-shape payload validation for ``llm_*`` connectors.
#
# Pinned by plan ``llm_provider_keys_per_user_migration`` Pass 1.4 +
# §Data contracts: the v1 payload is fixed to
# ``{"api_key": "<non-empty string>"}``. Extra keys are rejected so a
# typo in the dashboard or a malformed migration script cannot silently
# create a row whose runtime resolution would later 503 on the
# unrecognised shape. This validation is route-layer only — the service
# layer remains agnostic to per-connector payload shape so future
# vendors can land their own payloads with their own validators.
#
# 422 ``invalid_llm_payload`` is the chosen status: the connector id is
# valid (registered, well-formed), but the body shape is wrong, which
# is the standard FastAPI semantics for unprocessable-but-syntactically-
# valid JSON. ``message`` carries the cause verbatim so the dashboard
# can surface a helpful error without round-tripping.
# ---------------------------------------------------------------------------


_LLM_CONNECTOR_IDS: frozenset[str] = frozenset(LLM_CONNECTOR_BY_ENV.values())


def _validate_llm_payload(connector: str, payload: dict) -> None:
    """Raise ``HTTPException(422, invalid_llm_payload)`` for malformed LLM bodies.

    No-op for connectors outside the ``llm_*`` namespace — those keep
    their existing per-connector validation (currently inline in the
    service or deferred to the runtime resolver).
    """
    if connector not in _LLM_CONNECTOR_IDS:
        return

    def _bad(message: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_llm_payload",
                "connector": connector,
                "message": message,
            },
        )

    if not isinstance(payload, dict):
        raise _bad("payload must be a JSON object")
    extra = set(payload.keys()) - {"api_key"}
    if extra:
        raise _bad(
            f"payload must contain exactly one key 'api_key'; unexpected keys: {sorted(extra)}"
        )
    if "api_key" not in payload:
        raise _bad("payload missing required key 'api_key'")
    api_key = payload["api_key"]
    if not isinstance(api_key, str):
        raise _bad(f"payload['api_key'] must be a string (got {type(api_key).__name__})")
    if not api_key.strip():
        raise _bad("payload['api_key'] must be a non-empty string after strip()")


def _reject_operator_managed(connector: str) -> None:
    """422 for connectors that only their dedicated admin surface may write.

    ``system_smtp`` must be in the registry for its own admin route's
    ``require_registered`` gate to pass — and registration is what makes a
    connector writable at **every** tier. So the same edit that enables
    ``PUT /api/v1/admin/system-mail`` would, unguarded, also open
    ``PUT /api/v1/me/connector-credentials/system_smtp`` to every household
    member. That is not merely an untidy extra row:
    :func:`services.credential_tiers.resolve_runtime_credential` walks
    **user → household → system**, so a member-planted row would take
    precedence over the relay the admin proved with a Send test.

    Called from every PUT and DELETE on all four routers (LUM-65). GET and
    list stay open — an operator inspecting or cleaning up a stale row is not
    a write, and the projections never carry payload.
    """
    if connector in OPERATOR_MANAGED_CONNECTORS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "use_system_mail_admin",
                "connector": connector,
                "message": (
                    f"{connector!r} is operator-managed; configure it at "
                    "Admin → System mail (/api/v1/admin/system-mail)."
                ),
            },
        )


def _credential_unavailable_503() -> HTTPException:
    """503 body shape for decrypt failure / key unavailability.

    Body intentionally carries no per-row context (no ciphertext, no
    key fingerprint) — the operator-facing detail lives in the server
    log emitted by :func:`services.connector_credentials._decrypt_payload`.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "credential_unavailable"},
    )


# ---------------------------------------------------------------------------
# User-facing routes — /api/v1/me/connector-credentials
# ---------------------------------------------------------------------------


@router.get("/registry")
def list_connector_registry() -> dict:
    """List the registered connector ids + descriptions for UI dropdowns.

    Available to any authenticated caller (admins are also users —
    :func:`require_user` admits both). The wire shape is frozen by the
    plan ``credential_management_ux`` D2:
    ``{"items": [{"id": "...", "description": "..."}, ...]}``.

    Static-route order matters: this endpoint is declared BEFORE the
    ``/{connector}`` route below so FastAPI's path-matching does not
    interpret ``/registry`` as a connector id and try to look up a
    credential row for it.

    No ``response_model`` — the dict shape is small and stable, and
    keeping the endpoint dependency-free of additional Pydantic
    classes matches the convention deviation documented in the plan
    §Data contracts.
    """
    return {"items": iter_registered_with_descriptions()}


@router.get(
    "",
    response_model=ConnectorCredentialList,
)
def list_my_credentials(request: Request) -> ConnectorCredentialList:
    """Enumerate the caller's connector credential rows (metadata only).

    No registry filtering — historical-but-still-stored connectors
    appear here so the dashboard / operator UI can offer a delete
    affordance for stale rows.
    """
    caller = get_user(request)
    records = ccs.list_records(caller.user_id)
    return ConnectorCredentialList(items=[_to_public(r) for r in records])


@router.get(
    "/{connector}",
    response_model=ConnectorCredentialPublic,
)
def get_my_credential(
    connector: str,
    request: Request,
    instance: str = Query(""),
) -> ConnectorCredentialPublic:
    """Return metadata for one of the caller's credential rows.

    Format-strict only (matches the service contract for
    :func:`services.connector_credentials.get_record`): unknown
    registry membership does not 422 here, so admins/users can
    inspect stale rows. Plaintext is **never** returned by this
    route — there is no decrypt path on the credential-management
    surface. ``instance`` (LUM-170) selects a specific mailbox of a
    multi-instance connector (default ``""`` = the single-instance row).
    """
    caller = get_user(request)
    try:
        record = ccs.get_record(caller.user_id, connector, instance=instance)
    except ccs.InstanceNotAllowed as exc:
        raise _instance_error_422(connector, exc) from exc
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    if record is None:
        raise _connector_not_configured_404(connector)
    return _to_public(record)


@router.post(
    "/{connector}/test",
    response_model=ConnectorTestResponse,
    dependencies=[Depends(require_same_origin)],
)
def test_my_credential(connector: str, request: Request) -> ConnectorTestResponse:
    """Test whether the caller's stored credential for ``connector`` works.

    **A failed test is a ``200``, never a ``5xx``** — the same contract
    ``POST /me/email-sources/test`` established. "Your paperless server rejected
    this token" is an ordinary answer to an ordinary question, not a server
    error, and a 5xx would make the UI render an outage where there is a typo.

    No body. No decrypt path is added: the probe decrypts *inside*
    ``load_connection``, server-side, and hands back a tri-state verdict. No
    payload value and no payload key name crosses the wire.
    """
    caller = get_user(request)
    # FIRST — before the registry check, before any decrypt, before any
    # outbound call. A `429` must mean nothing happened, and this route's whole
    # cost is the outbound request it is refusing to make. Every attempt counts,
    # including one against an unknown id.
    if not _connector_test_limiter.check(caller.user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited"},
        )

    try:
        validate_format(connector)
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    if not is_registered(connector):
        raise _unknown_connector_404(connector)

    try:
        outcome = probes.probe(connector, caller.user_id)
    except probes.OperatorManagedConnector:
        # `system_smtp` is hidden from GET /registry so no card renders for it,
        # but it passes registry membership and is reachable by direct POST.
        # Refuse with the same body its PUT and DELETE siblings return rather
        # than becoming the one verb on this surface that answers.
        _reject_operator_managed(connector)
        raise  # pragma: no cover - the call above always raises for this set
    except Exception as exc:  # noqa: BLE001 - this catch IS the "never 5xx" contract
        # Without an owner, "never 5xx on a probe failure" is a wish: any
        # exception type nobody enumerated becomes a 500. Type only in the log —
        # `str(exc)` off this surface is known to embed the configured host.
        _log.warning(
            "connector_test.unexpected connector=%s type=%s", connector, type(exc).__name__
        )
        outcome = probes.unreachable()

    return ConnectorTestResponse(ok=outcome.ok, code=outcome.code, detail=outcome.detail)


@router.put(
    "/{connector}",
    response_model=ConnectorCredentialPublic,
    dependencies=[Depends(require_same_origin)],
)
def put_my_credential(
    connector: str,
    body: PutConnectorCredentialRequest,
    request: Request,
    instance: str = Query(""),
) -> ConnectorCredentialPublic:
    """UPSERT one of the caller's credential rows. ``actor="self"``.

    Returns ``200`` for both create and update — the response body's
    ``created_at`` / ``updated_at`` distinguishes the two if the
    caller cares; status code does not (see plan §API routes "PUT
    status-code rationale"). Registry-strict by service contract:
    unknown connectors fail closed with ``422``. ``instance`` (LUM-170)
    keys a specific mailbox for a multi-instance connector (imap keys per
    ``sources.id``); a multi-instance connector requires a non-empty
    instance and a single-instance one rejects it (``422``).
    """
    caller = get_user(request)
    _reject_operator_managed(connector)
    _validate_llm_payload(connector, body.payload)
    if connector == "imap" and instance:
        try:
            validate_imap_instance_payload_against_source(caller.user_id, instance, body.payload)
        except ImapHostMismatchError:
            raise _imap_host_mismatch_422()
        except ImapSourceNotFoundError:
            raise _imap_source_not_found_422()
        except ImapSourceUrlInvalidError:
            raise _imap_source_url_invalid_422()
        except ValueError as exc:
            raise _bad_connector_id_400(connector, exc) from exc
    try:
        record = ccs.put_payload(
            caller.user_id,
            connector,
            body.payload,
            actor="self",
            instance=instance,
        )
    except ccs.InstanceNotAllowed as exc:
        raise _instance_error_422(connector, exc) from exc
    except UnknownConnector as exc:
        # MUST come before ValueError — UnknownConnector subclasses
        # ValueError (defined in connectors.registry) so a flat `except
        # ValueError` would shadow it and 400-instead-of-422 every
        # unregistered-connector PUT.
        raise _unknown_connector_422(connector) from exc
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    except ccs.CredentialUnavailable as exc:
        # Surface as 503 even though the service raises this on decrypt;
        # _encrypt_payload itself does not raise CredentialUnavailable,
        # but the route still funnels every CredentialUnavailable through
        # the same body shape so future encrypt-side variants stay correct.
        raise _credential_unavailable_503() from exc
    return _to_public(record)


@router.patch(
    "/{connector}",
    response_model=ConnectorCredentialPublic,
    dependencies=[Depends(require_same_origin)],
)
def patch_my_credential(
    connector: str,
    body: PatchConnectorCredentialRequest,
    request: Request,
) -> ConnectorCredentialPublic:
    """Switch one of the caller's stored credentials on or off. ``actor="self"``.

    **`PATCH`, deliberately not `PUT`.** On this surface `PUT` means
    whole-payload replacement — the semantics behind LUM-846, where forms that
    omitted a secret silently destroyed it. Reusing that verb for a boolean
    flag would be the third time it bit this feature.

    **Disabling does not delete.** The credential stays encrypted in its row;
    re-enabling resumes without re-entering the secret.

    The path carries no ``user_id``: the caller's own row is the only one
    reachable, which is what makes this safe without an ownership check.

    * ``404`` when there is no row — nothing saved yet to enable or disable.
    * ``422`` for a multi-instance connector. There is no ``instance``
      parameter here, so a PATCH could only ever address the legacy
      ``instance=""`` row of a per-mailbox connector; disabling one of those
      would raise inside mail polling with nothing to explain it. Refused
      explicitly rather than half-supported.
    * ``422`` for ``system_smtp``, like every sibling verb.
    """
    caller = get_user(request)
    _reject_operator_managed(connector)
    try:
        if is_multi_instance(connector):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "instance_required",
                    "message": (
                        f"{connector!r} is configured per mailbox; enable or disable it "
                        "from the Email signals page."
                    ),
                },
            )
        record = ccs.set_enabled(
            caller.user_id,
            connector,
            enabled=body.enabled,
            actor="self",
        )
    except UnknownConnector as exc:
        raise _unknown_connector_422(connector) from exc
    except ccs.InstanceNotAllowed as exc:
        raise _instance_error_422(connector, exc) from exc
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    if record is None:
        raise _connector_not_configured_404(connector)
    return _to_public(record)


@router.delete(
    "/{connector}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_same_origin)],
)
def delete_my_credential(
    connector: str,
    request: Request,
    instance: str = Query(""),
) -> Response:
    """Delete one of the caller's credential rows. ``actor="self"``.

    Format-strict only — stale historical rows can be cleaned up
    without first re-registering the connector. Returns ``204`` on
    delete, ``404`` (``connector_not_configured``) when the row was
    already gone (idempotency boundary). ``instance`` (LUM-170) targets a
    specific mailbox of a multi-instance connector.
    """
    caller = get_user(request)
    _reject_operator_managed(connector)
    try:
        deleted = ccs.delete_payload(caller.user_id, connector, actor="self", instance=instance)
    except ccs.InstanceNotAllowed as exc:
        raise _instance_error_422(connector, exc) from exc
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    if not deleted:
        raise _connector_not_configured_404(connector)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Admin routes — /api/v1/admin/users/{user_id}/connector-credentials
# ---------------------------------------------------------------------------


def _require_known_user(user_id: str) -> None:
    """Raise ``404`` when the path's ``user_id`` is not in the users table.

    Mirrors :mod:`routes.mcp_tokens`'s admin info-leak guard: returning
    ``404`` rather than ``403`` keeps the response indistinguishable
    from a missing row to anyone without admin powers (admin-gating
    already prevents non-admins from reaching this branch).
    """
    if users_service.get_user_by_id(user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "user_not_found", "user_id": user_id},
        )


def _admin_actor(request: Request) -> str:
    """Build the ``actor`` string for admin-side mutations.

    Format pinned by the migration's ``CHECK`` constraint and the
    service's :func:`_actor_str` regex: ``admin:<actor_user_id>``.
    Sourced from the authenticated caller (NOT the path's ``user_id``,
    which is the **target** of the action, not the actor).
    """
    caller = get_user(request)
    return f"admin:{caller.user_id}"


@admin_router.get(
    "",
    response_model=ConnectorCredentialList,
)
def admin_list_user_credentials(
    user_id: str,
) -> ConnectorCredentialList:
    """Admin-only enumeration of one user's connector credential rows.

    Returns ``404`` when the target user does not exist (parity with
    :func:`routes.mcp_tokens.admin_list_user_tokens`).
    """
    _require_known_user(user_id)
    records = ccs.list_records(user_id)
    return ConnectorCredentialList(items=[_to_public(r) for r in records])


@admin_router.get(
    "/{connector}",
    response_model=ConnectorCredentialPublic,
)
def admin_get_user_credential(
    user_id: str,
    connector: str,
    instance: str = Query(""),
) -> ConnectorCredentialPublic:
    """Admin-only metadata read for ``(user_id, connector, instance)``.

    Format-strict only — admins can inspect stale-but-stored rows so
    they can choose to delete them. Plaintext is **never** returned.
    ``instance`` (LUM-170) targets a specific mailbox of a multi-instance
    connector.
    """
    _require_known_user(user_id)
    try:
        record = ccs.get_record(user_id, connector, instance=instance)
    except ccs.InstanceNotAllowed as exc:
        raise _instance_error_422(connector, exc) from exc
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    if record is None:
        raise _connector_not_configured_404(connector)
    return _to_public(record)


@admin_router.put(
    "/{connector}",
    response_model=ConnectorCredentialPublic,
    dependencies=[Depends(require_same_origin)],
)
def admin_put_user_credential(
    user_id: str,
    connector: str,
    body: PutConnectorCredentialRequest,
    request: Request,
    instance: str = Query(""),
) -> ConnectorCredentialPublic:
    """Admin UPSERTs a credential row on behalf of ``user_id``.

    ``actor`` becomes ``admin:<caller.user_id>`` so the audit trail
    distinguishes self-service writes (``actor="self"``) from
    operator interventions. ``instance`` (LUM-170) keys a specific mailbox.
    """
    _require_known_user(user_id)
    _reject_operator_managed(connector)
    _validate_llm_payload(connector, body.payload)
    if connector == "imap" and instance:
        try:
            validate_imap_instance_payload_against_source(user_id, instance, body.payload)
        except ImapHostMismatchError:
            raise _imap_host_mismatch_422()
        except ImapSourceNotFoundError:
            raise _imap_source_not_found_422()
        except ImapSourceUrlInvalidError:
            raise _imap_source_url_invalid_422()
        except ValueError as exc:
            raise _bad_connector_id_400(connector, exc) from exc
    actor = _admin_actor(request)
    try:
        record = ccs.put_payload(user_id, connector, body.payload, actor=actor, instance=instance)
    except ccs.InstanceNotAllowed as exc:
        raise _instance_error_422(connector, exc) from exc
    except UnknownConnector as exc:
        raise _unknown_connector_422(connector) from exc
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    except ccs.CredentialUnavailable as exc:
        raise _credential_unavailable_503() from exc
    return _to_public(record)


@admin_router.delete(
    "/{connector}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_same_origin)],
)
def admin_delete_user_credential(
    user_id: str,
    connector: str,
    request: Request,
    instance: str = Query(""),
) -> Response:
    """Admin deletes a credential row owned by ``user_id``.

    Format-strict only (parity with the user-facing DELETE) — admins
    must be able to clean up stale rows without first re-registering
    the connector. ``instance`` (LUM-170) targets a specific mailbox.
    """
    _require_known_user(user_id)
    _reject_operator_managed(connector)
    actor = _admin_actor(request)
    try:
        deleted = ccs.delete_payload(user_id, connector, actor=actor, instance=instance)
    except ccs.InstanceNotAllowed as exc:
        raise _instance_error_422(connector, exc) from exc
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    if not deleted:
        raise _connector_not_configured_404(connector)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Household + instance/system tier admin routes (ADR
# ``credential_scopes_shared_system``).
#
# Both routers are admin-only (router-level ``Depends(require_admin)`` plus
# ``Depends(require_same_origin)`` on every mutating verb). The same domain →
# HTTP error mapping applies — ``UnknownConnector → 422``,
# ``ValueError → 400``, ``CredentialUnavailable → 503``,
# ``None → 404 connector_not_configured``.
#
# ``actor`` is built via ``_admin_actor(request)`` (``admin:<caller.user_id>``)
# for both PUT and DELETE; the service layer parses that into the bare
# ``user_id`` for ``audit_log.user_id`` (see
# ``services.credential_tiers._extract_admin_caller_user_id``).
# ---------------------------------------------------------------------------


def _to_household_public(
    record: cts.HouseholdCredentialRecord,
) -> HouseholdConnectorCredentialPublic:
    """Project a household record onto the wire model."""
    return HouseholdConnectorCredentialPublic.model_validate(record.__dict__)


def _to_system_public(
    record: cts.InstanceSystemCredentialRecord,
) -> InstanceSystemConnectorCredentialPublic:
    """Project an instance/system record onto the wire model."""
    return InstanceSystemConnectorCredentialPublic.model_validate(record.__dict__)


# --- Household tier ---------------------------------------------------------


@household_admin_router.get(
    "",
    response_model=HouseholdConnectorCredentialList,
)
def admin_list_household_credentials() -> HouseholdConnectorCredentialList:
    """Admin-only enumeration of household-tier credential rows.

    No registry filtering — historical-but-still-stored connectors
    appear so admins can offer a delete affordance for stale rows.
    """
    records = cts.household_list_records()
    return HouseholdConnectorCredentialList(
        items=[_to_household_public(r) for r in records],
    )


@household_admin_router.get(
    "/{connector}",
    response_model=HouseholdConnectorCredentialPublic,
)
def admin_get_household_credential(
    connector: str,
) -> HouseholdConnectorCredentialPublic:
    """Admin-only metadata read for a single household credential row."""
    try:
        record = cts.household_get_record(connector)
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    if record is None:
        raise _connector_not_configured_404(connector)
    return _to_household_public(record)


@household_admin_router.put(
    "/{connector}",
    response_model=HouseholdConnectorCredentialPublic,
    dependencies=[Depends(require_same_origin)],
)
def admin_put_household_credential(
    connector: str,
    body: PutConnectorCredentialRequest,
    request: Request,
) -> HouseholdConnectorCredentialPublic:
    """Admin UPSERTs a household-tier credential row.

    ``actor`` becomes ``admin:<caller.user_id>``; ``audit_log.user_id``
    will carry the bare admin user_id; ``input_summary`` carries
    ``tier="household"``.
    """
    _reject_operator_managed(connector)
    _validate_llm_payload(connector, body.payload)
    actor = _admin_actor(request)
    try:
        record = cts.household_put_payload(connector, body.payload, actor=actor)
    except UnknownConnector as exc:
        raise _unknown_connector_422(connector) from exc
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    except ccs.CredentialUnavailable as exc:
        raise _credential_unavailable_503() from exc
    return _to_household_public(record)


@household_admin_router.delete(
    "/{connector}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_same_origin)],
)
def admin_delete_household_credential(
    connector: str,
    request: Request,
) -> Response:
    """Admin deletes a household-tier credential row.

    Format-strict only — admins must be able to clean up stale rows
    without first re-registering the connector. ``404`` when the row
    was already gone (idempotency boundary).
    """
    _reject_operator_managed(connector)
    actor = _admin_actor(request)
    try:
        deleted = cts.household_delete_payload(connector, actor=actor)
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    if not deleted:
        raise _connector_not_configured_404(connector)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Instance / system tier ------------------------------------------------


@system_admin_router.get(
    "",
    response_model=InstanceSystemConnectorCredentialList,
)
def admin_list_system_credentials() -> InstanceSystemConnectorCredentialList:
    """Admin-only enumeration of instance/system-tier credential rows."""
    records = cts.system_list_records()
    return InstanceSystemConnectorCredentialList(
        items=[_to_system_public(r) for r in records],
    )


@system_admin_router.get(
    "/{connector}",
    response_model=InstanceSystemConnectorCredentialPublic,
)
def admin_get_system_credential(
    connector: str,
) -> InstanceSystemConnectorCredentialPublic:
    """Admin-only metadata read for a single instance/system credential row."""
    try:
        record = cts.system_get_record(connector)
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    if record is None:
        raise _connector_not_configured_404(connector)
    return _to_system_public(record)


@system_admin_router.put(
    "/{connector}",
    response_model=InstanceSystemConnectorCredentialPublic,
    dependencies=[Depends(require_same_origin)],
)
def admin_put_system_credential(
    connector: str,
    body: PutConnectorCredentialRequest,
    request: Request,
) -> InstanceSystemConnectorCredentialPublic:
    """Admin UPSERTs an instance/system-tier credential row.

    ``audit_log.user_id`` will carry the bare admin user_id;
    ``input_summary`` carries ``tier="system"``.
    """
    _reject_operator_managed(connector)
    _validate_llm_payload(connector, body.payload)
    actor = _admin_actor(request)
    try:
        record = cts.system_put_payload(connector, body.payload, actor=actor)
    except UnknownConnector as exc:
        raise _unknown_connector_422(connector) from exc
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    except ccs.CredentialUnavailable as exc:
        raise _credential_unavailable_503() from exc
    return _to_system_public(record)


@system_admin_router.delete(
    "/{connector}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_same_origin)],
)
def admin_delete_system_credential(
    connector: str,
    request: Request,
) -> Response:
    """Admin deletes an instance/system-tier credential row.

    Format-strict only — admins must be able to clean up stale rows
    without first re-registering the connector.
    """
    _reject_operator_managed(connector)
    actor = _admin_actor(request)
    try:
        deleted = cts.system_delete_payload(connector, actor=actor)
    except ValueError as exc:
        raise _bad_connector_id_400(connector, exc) from exc
    if not deleted:
        raise _connector_not_configured_404(connector)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
