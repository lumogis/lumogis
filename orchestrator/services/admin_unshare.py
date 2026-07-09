# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Admin governance: unshare another member's shared item (LUM-584).

Every member can unshare **their own** item today via
``routes/scope.py::_unpublish_one`` (owner-only fetch: ``WHERE <pk>=%s AND
user_id=%s AND scope='personal'``). This module adds a **separate,
admin-gated** capability to retract *another* member's share on behalf of
the household — retract-only governance, distinct from and never widening
the owner path.

Security properties (see the LUM-584 plan §Security decisions):

* **No existence oracle.** The lookup queries the **shared projection row
  only** (``WHERE published_from=%s AND scope='shared'``) — it never reads a
  member's private ``personal`` source row. A pk that is not currently
  shared returns the same opaque "not found" as a pk that does not exist,
  so an admin cannot probe whether member B holds a never-shared private
  item at a given pk. The shared projection carries ``user_id`` = the
  owner, so the true owner is resolved from it directly (publish is
  owner-only, so the projection's ``user_id`` is the source owner).
* **Retract-only.** This path removes a share; it never creates one.
* **Honest teardown.** ``unproject_*`` deletes the Postgres projection
  (authoritative) and best-effort deletes the Qdrant mirror — but the
  shared Qdrant primitive (``projection._qdrant_delete_safe``) *swallows*
  backend failures. Because retraction is privacy-critical, this path adds
  a **post-teardown Qdrant count** (``count_where``): if any shared point
  for the source survives, the call fails rather than claiming success.
* **Audited, non-silently.** Every retraction writes an ``audit_log`` row
  (acting admin + source owner + resource). An audit-write failure fails
  the request loudly — an untraceable governance override is worse than a
  failed one.
* **Parameterised SQL only.** ``resource`` selects a fixed registry entry;
  table / column names are server constants, never interpolated from the
  path param. Only the pk is ``%s``-bound.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from typing import Optional

from auth import UserContext
from models.actions import AuditEntry

import config
from actions.audit import write_audit
from services.sharing_registry import SHAREABLE_RESOURCES as _RESOURCE
from services.sharing_registry import short_label as _short

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain exceptions (mapped to HTTP by routes/admin_sharing.py)
# ---------------------------------------------------------------------------


class UnknownResource(ValueError):
    """The resource type is not a known shareable type (→ 400)."""


class SharedItemNotFound(Exception):
    """No active shared projection exists for the pk (→ opaque 404).

    Deliberately indistinguishable from "source does not exist" so the
    route cannot be used as an existence oracle over private items.
    """


class TeardownIncomplete(Exception):
    """The shared Qdrant mirror survived teardown (→ 500 / unshared:false).

    Raised when the post-teardown count still finds shared points, or when
    the verification count itself could not be performed — in either case
    we must not claim success on a privacy-critical retraction.
    """


class AuditWriteFailed(Exception):
    """The governance action succeeded but could not be recorded (→ 500)."""


# The shareable-type registry (table / pk_col / pk_type / unproject / collection
# / label_col) is the canonical ``services.sharing_registry.SHAREABLE_RESOURCES``,
# imported above as ``_RESOURCE``. Server constants only — never interpolated
# from request input.


def _coerce_pk(pk: str, pk_type: str) -> Any:
    """Return the pk in the column's native type.

    INTEGER-PK resources (``file_index``) need an int for both the SQL
    lookup and the Qdrant ``published_from`` match (chunk payloads store it
    as int). A non-integer pk for an int resource cannot reference a shared
    row → treated as not-found (opaque), never a 500.
    """
    if pk_type == "int":
        try:
            return int(pk)
        except (TypeError, ValueError):
            raise SharedItemNotFound("files", pk)
    return str(pk)


def _qdrant_shared_remaining(collection: str, pk_val: Any) -> int:
    """Count shared Qdrant points still pointing at the source after teardown.

    A verification-count failure means we cannot prove the mirror is gone;
    for a privacy-critical retraction that is a teardown failure, not a
    success — surfaced as ``TeardownIncomplete`` by the caller.
    """
    vs = config.get_vector_store()
    return vs.count_where(
        collection,
        {
            "must": [
                {"key": "published_from", "match": {"value": pk_val}},
                {"key": "scope", "match": {"value": "shared"}},
            ]
        },
    )


def admin_unshare(*, actor: UserContext, resource: str, pk: str) -> dict:
    """Retract another member's household share (admin governance).

    Owner-independent by design — there is **no** ``user_id=caller``
    predicate anywhere in this path; the admin gate lives on the route.
    Idempotency is bounded by the shared-projection lookup: a second call on
    an already-unshared item raises ``SharedItemNotFound`` (404), because the
    share no longer exists to retract.
    """
    cfg = _RESOURCE.get(resource)
    if cfg is None:
        raise UnknownResource(resource)

    pk_val = _coerce_pk(pk, cfg["pk_type"])

    ms = config.get_metadata_store()
    # Look at the SHARED projection only — never the private personal source.
    # ``published_from`` + ``scope='shared'`` is the active-share predicate;
    # the projection's ``user_id`` is the source owner (publish is owner-only).
    shared = ms.fetch_one(
        f"SELECT user_id FROM {cfg['table']} "  # noqa: S608 — table is a fixed registry constant
        f"WHERE published_from = %s AND scope = 'shared'",
        (pk_val,),
    )
    source_owner_id: Optional[str] = shared.get("user_id") if shared else None

    if shared is None:
        # No Postgres shared row. Two cases, kept indistinguishable to avoid an
        # existence oracle:
        #   (a) the item was never shared (or does not exist) → genuine 404;
        #   (b) an earlier retract deleted the Postgres row but its Qdrant
        #       mirror survived (the delete primitive swallows failures) →
        #       an orphan we must still be able to clean up.
        # The count is scope='shared'-filtered, so a private/never-shared item
        # has zero shared points → identical opaque 404 (no private-item leak).
        try:
            orphaned = _qdrant_shared_remaining(cfg["collection"], pk_val)
        except Exception as exc:  # noqa: BLE001 — cannot verify == cannot claim success
            _log.error(
                "admin_unshare: Qdrant orphan check failed resource=%s pk=%s — %s",
                resource,
                pk,
                exc,
            )
            raise TeardownIncomplete(resource, pk, "verification_failed") from exc
        if orphaned == 0:
            raise SharedItemNotFound(resource, pk)
        # Fall through: re-run teardown to clear the orphaned mirror. The owner
        # is unknown here (the Postgres row that carried it is already gone);
        # the first attempt's audit row recorded it.

    # Write-ahead governance intent — audit BEFORE the destructive teardown so
    # that every teardown that happens IS recorded. A failed audit here tears
    # nothing down (the shared row remains → the admin can retry). A retry that
    # cleans an orphan writes a second (owner-less) row; duplicate rows are
    # benign and truthfully record each attempt. This is the deliberate
    # resolution of "an untraceable governance override is worse than a failed
    # one": we never delete the share without first recording the intent.
    audit_id = write_audit(
        AuditEntry(
            action_name="admin_unshare",
            connector="admin",
            mode="DO",
            input_summary=f"admin_unshare {resource}/{pk}",
            result_summary=json.dumps(
                {
                    "resource_type": resource,
                    "resource_id": str(pk),
                    "source_owner_id": source_owner_id,
                }
            ),
            user_id=actor.user_id,
        )
    )
    if audit_id is None:
        _log.error(
            "admin_unshare: audit write failed resource=%s pk=%s admin=%s",
            resource,
            pk,
            actor.user_id,
        )
        raise AuditWriteFailed(resource, pk)

    # Teardown: Postgres projection delete (authoritative) + best-effort Qdrant
    # point delete via the shared primitive. Idempotent, so an orphan retry is
    # safe. A hard failure (e.g. the file-chunk delete_where does not swallow)
    # must map to the honest 500, never escape as an uncaught error.
    try:
        cfg["unproject"](pk_val)
    except Exception as exc:  # noqa: BLE001 — surface as an honest, retriable failure
        _log.error(
            "admin_unshare: teardown failed resource=%s pk=%s — %s",
            resource,
            pk,
            exc,
        )
        raise TeardownIncomplete(resource, pk, "unproject_failed") from exc

    # Post-teardown verification (the shared primitive swallows Qdrant
    # failures; retraction is privacy-critical so we must confirm). A survivor
    # yields a 500 that is now genuinely retriable — a later call sees no
    # Postgres row but a non-zero orphan count and re-runs the teardown.
    try:
        remaining = _qdrant_shared_remaining(cfg["collection"], pk_val)
    except Exception as exc:  # noqa: BLE001 — cannot verify == cannot claim success
        _log.error(
            "admin_unshare: Qdrant teardown verification failed resource=%s pk=%s — %s",
            resource,
            pk,
            exc,
        )
        raise TeardownIncomplete(resource, pk, "verification_failed") from exc
    if remaining > 0:
        _log.error(
            "admin_unshare: %d shared Qdrant point(s) survived teardown resource=%s pk=%s",
            remaining,
            resource,
            pk,
        )
        raise TeardownIncomplete(resource, pk, remaining)

    _log.info(
        "admin_unshare: admin=%s retracted %s/%s owner=%s",
        actor.user_id,
        resource,
        pk,
        source_owner_id,
    )
    return {
        "resource_type": resource,
        "resource_id": str(pk),
        "source_owner_id": source_owner_id,
        "unshared": True,
    }


def admin_list_shared_items(*, limit: int = 200) -> list[dict]:
    """List every household shared item with its source pk + owner (admin-only).

    The admin UI needs the **source pk** (``published_from``) to call the
    unshare route — no publish response exposes it (``scope.py`` elides it
    deliberately). Household-wide (not owner-scoped), admin-gated at the
    route. Uses **N per-arm queries** with per-type failure isolation (one
    resource type's query error does not blank the whole list), mirroring
    LUM-583's aggregate.
    """
    ms = config.get_metadata_store()
    items: list[dict] = []
    for rtype, cfg in _RESOURCE.items():
        try:
            rows = ms.fetch_all(
                f"SELECT published_from, user_id, {cfg['label_col']} AS label "  # noqa: S608 — fixed registry constants
                f"FROM {cfg['table']} "
                f"WHERE scope = 'shared' AND published_from IS NOT NULL "
                f"ORDER BY published_from LIMIT %s",
                (limit,),
            )
        except Exception:  # noqa: BLE001 — per-arm isolation; one type must not blank the list
            _log.exception("admin_list_shared_items: query failed for %s", rtype)
            continue
        for r in rows:
            items.append(
                {
                    "resource_type": rtype,
                    "resource_id": str(r["published_from"]),
                    "source_owner_id": r.get("user_id"),
                    "label": _short(r.get("label")),
                }
            )
    return items
