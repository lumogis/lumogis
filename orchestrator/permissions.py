# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Ask/Do permission enforcement (per-user since migration 016).

Every tool call passes through ``check_permission`` before execution.
Connectors default to ASK mode (read-only). DO mode is explicitly
enabled per-user, per-connector via PUT /api/v1/me/permissions/{connector}.

Per-user lift (plan ``per_user_connector_permissions``, audit A2): all
state in ``connector_permissions`` and ``routine_do_tracking`` is keyed
by ``user_id``. The cache is keyed by ``(user_id, connector)``. The
``user_id`` argument to every public function is keyword-only and
required (Phase 3 contract).

Disabled-user latent footgun
----------------------------
Direct callers of :func:`get_connector_mode` outside the established
``executor.py → check_permission → get_connector_mode`` fan-in MUST
either (a) flow through the standard auth gate AND accept the
JWT-TTL access window, OR (b) re-check
``services.users.get_user_by_id(user_id).disabled`` before granting
effect if they need stronger guarantees. ``auth.py`` does NO
per-request DB lookup of ``users.disabled``: a disabled user's
already-issued access JWT remains valid for ``ACCESS_TOKEN_TTL_SECONDS``
(default 900s). MCP-bearer requests stop immediately via cascade
revocation. Promoting the disabled-check into ``get_connector_mode``
is deferred follow-up #5.

SCOPE-EXEMPT note
-----------------
Both ``connector_permissions`` and ``routine_do_tracking`` are
per-user but scope-less (no ``scope`` column). Raw ``WHERE user_id =``
SQL in this file carries the ``-- SCOPE-EXEMPT: ...`` trailer for
inline documentation. The grep gate at
``tests/test_no_raw_user_id_filter_outside_admin.py`` does NOT scan
this module (orchestrator package root is out of its scanned roots),
so the markers are documentation, not load-bearing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone

import config

_log = logging.getLogger(__name__)

_mode_cache: dict[tuple[str, str], str] = {}

_DEFAULT_MODE = "ASK"
_VALID_MODES = {"ASK", "DO"}


def get_connector_mode(*, user_id: str, connector: str) -> str:
    """Resolve the effective mode for ``(user_id, connector)``.

    Cache-key is ``(user_id, connector)``. On cache miss, hits the DB;
    on no-row, returns :data:`_DEFAULT_MODE`. ``user_id`` is keyword-only
    and required (Phase 3 contract); raises :class:`TypeError` on missing
    or empty ``user_id``.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("get_connector_mode: user_id (keyword-only) is required")
    key = (user_id, connector)
    if key in _mode_cache:
        return _mode_cache[key]
    store = config.get_metadata_store()
    row = store.fetch_one(
        "SELECT mode FROM connector_permissions "
        "WHERE user_id = %s AND connector = %s "
        "-- SCOPE-EXEMPT: connector_permissions has no scope column",
        (user_id, connector),
    )
    mode = row["mode"] if row else _DEFAULT_MODE
    _mode_cache[key] = mode
    return mode


def invalidate_cache(user_id: str, connector: str) -> None:
    """Drop a single ``(user_id, connector)`` slot."""
    _mode_cache.pop((user_id, connector), None)


def clear_cache_for_user(user_id: str) -> None:
    """Drop every cache slot owned by ``user_id``.

    Iterates over a snapshot of keys to avoid ``RuntimeError: dictionary
    changed size during iteration``.
    """
    for key in list(_mode_cache.keys()):
        if key[0] == user_id:
            _mode_cache.pop(key, None)


def check_permission(
    connector: str,
    action_type: str,
    is_write: bool,
    *,
    user_id: str,
) -> bool:
    """Permission gate. ``user_id`` is keyword-only and required (Phase 3)."""
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("check_permission: user_id (keyword-only) is required")

    mode = get_connector_mode(user_id=user_id, connector=connector)
    allowed = True
    if is_write and mode == "ASK":
        allowed = False
    log_action(
        connector=connector,
        action_type=action_type,
        mode=mode,
        allowed=allowed,
        user_id=user_id,
    )
    return allowed


def log_action(
    connector: str,
    action_type: str,
    mode: str,
    allowed: bool,
    *,
    user_id: str,
    input_summary: str | None = None,
    result_summary: str | None = None,
    reverse_action: str | None = None,
) -> None:
    """Append an action_log row. ``user_id`` is keyword-only and required.

    Plan §17 (D14, P3 §15): every audit row must carry the caller's
    ``user_id`` so the per-user view of "what did this account do" is
    actually queryable. The action_log table already has the column;
    this function is what writes it.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("log_action: user_id (keyword-only) is required")

    store = config.get_metadata_store()
    try:
        store.execute(
            """INSERT INTO action_log
               (user_id, connector, action_type, mode, allowed, input_summary,
                result_summary, reverse_action, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                user_id,
                connector,
                action_type,
                mode,
                allowed,
                input_summary,
                result_summary,
                reverse_action,
                datetime.now(timezone.utc),
            ),
        )
    except Exception:
        _log.exception("Failed to log action for %s/%s", connector, action_type)


def set_connector_mode(*, user_id: str, connector: str, mode: str) -> None:
    """UPSERT a per-user connector mode. Invalidates the cache slot."""
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("set_connector_mode: user_id (keyword-only) is required")
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid mode: {mode}. Must be one of {_VALID_MODES}")
    store = config.get_metadata_store()
    store.execute(
        "INSERT INTO connector_permissions (user_id, connector, mode) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id, connector) DO UPDATE "
        "SET mode = EXCLUDED.mode, updated_at = NOW()",
        (user_id, connector, mode),
    )
    invalidate_cache(user_id, connector)
    _log.info(
        "permission_changed user_id=%s connector=%s mode=%s",
        user_id,
        connector,
        mode,
    )


def delete_user_permission(*, user_id: str, connector: str) -> None:
    """Drop a per-user row, reverting that user/connector to the lazy default."""
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("delete_user_permission: user_id (keyword-only) is required")
    store = config.get_metadata_store()
    store.execute(
        "DELETE FROM connector_permissions "
        "WHERE user_id = %s AND connector = %s "
        "-- SCOPE-EXEMPT: connector_permissions has no scope column",
        (user_id, connector),
    )
    invalidate_cache(user_id, connector)
    _log.info(
        "permission_deleted user_id=%s connector=%s",
        user_id,
        connector,
    )


def seed_defaults() -> None:
    """No-op shim retained for backward compatibility.

    Pre-016 this seeded a global ``('filesystem-mcp', 'ASK')`` row.
    Per-user model relies on the lazy ``_DEFAULT_MODE = 'ASK'`` fallback,
    so no DB I/O is needed. Removed at next sweep.
    """
    _log.info(
        "seed_defaults: per-user model active; this call is a no-op shim "
        "and will be removed in a future release"
    )


def get_all_permissions() -> list[dict]:
    """Cross-user enumeration of every explicit per-user row.

    Returns ``[{"user_id": str, "connector": str, "mode": str}, ...]``
    sorted by ``(user_id, connector)``. Used by the admin enumeration
    endpoint and the legacy ``GET /permissions`` alias.
    """
    store = config.get_metadata_store()
    return store.fetch_all(
        "SELECT user_id, connector, mode FROM connector_permissions "
        "ORDER BY user_id, connector "
        "-- SCOPE-EXEMPT: connector_permissions has no scope column"
    )


def get_user_permissions(*, user_id: str) -> list[dict]:
    """Return one row per explicit per-user permission for ``user_id``.

    Returns ``[{"connector": str, "mode": str}, ...]`` sorted by connector.
    Connectors without an explicit row are NOT included; callers that
    want the lazy-default fan-out should use
    :func:`get_user_effective_permissions` instead.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("get_user_permissions: user_id (keyword-only) is required")
    store = config.get_metadata_store()
    return store.fetch_all(
        "SELECT connector, mode FROM connector_permissions "
        "WHERE user_id = %s ORDER BY connector "
        "-- SCOPE-EXEMPT: connector_permissions has no scope column",
        (user_id,),
    )


def get_user_effective_permissions(
    *,
    user_id: str,
    known_connectors: list[str],
) -> list[dict]:
    """Return one row per ``known_connector`` from the user's effective view.

    Each row is shaped as ``{"connector": str, "mode": str, "is_default": bool,
    "updated_at": datetime | None}``. Connectors without an explicit
    per-user row come back as ``is_default=True, mode=_DEFAULT_MODE,
    updated_at=None``.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("get_user_effective_permissions: user_id (keyword-only) is required")
    explicit = {row["connector"]: row for row in get_user_permissions(user_id=user_id)}
    out: list[dict] = []
    for connector in sorted(set(known_connectors)):
        row = explicit.get(connector)
        if row is not None:
            out.append(
                {
                    "connector": connector,
                    "mode": row["mode"],
                    "is_default": False,
                    "updated_at": row.get("updated_at"),
                }
            )
        else:
            out.append(
                {
                    "connector": connector,
                    "mode": _DEFAULT_MODE,
                    "is_default": True,
                    "updated_at": None,
                }
            )
    return out


def routine_check(
    *,
    user_id: str,
    connector: str,
    action_type: str,
) -> None:
    """Increment per-user approval count and fire ROUTINE_ELEVATION_READY.

    Threshold: ``approval_count >= 15`` with ``edit_count == 0``.
    Never auto-elevates -- only fires the hook so plugins can handle it.
    Hard-limited action_types are never elevated (enforced in executor.py).

    ``user_id`` is keyword-only and required (Phase 3): every elevation
    event is routed to the user whose action triggered it. Per-user
    counters since migration 016.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("routine_check: user_id (keyword-only) is required")
    try:
        store = config.get_metadata_store()
        store.execute(
            "INSERT INTO routine_do_tracking "
            "(user_id, connector, action_type, approval_count) "
            "VALUES (%s, %s, %s, 1) "
            "ON CONFLICT (user_id, connector, action_type) DO UPDATE "
            "SET approval_count = routine_do_tracking.approval_count + 1, "
            "    updated_at = NOW()",
            (user_id, connector, action_type),
        )
        row = store.fetch_one(
            "SELECT approval_count, edit_count, auto_approved "
            "FROM routine_do_tracking "
            "WHERE user_id = %s AND connector = %s AND action_type = %s "
            "-- SCOPE-EXEMPT: routine_do_tracking has no scope column",
            (user_id, connector, action_type),
        )
        if row and int(row["approval_count"]) >= 15 and int(row["edit_count"]) == 0:
            import hooks
            from events import Event

            hooks.fire(
                Event.ROUTINE_ELEVATION_READY,
                connector=connector,
                action_type=action_type,
                approval_count=row["approval_count"],
                user_id=user_id,
            )
            _log.info(
                "Routine elevation ready: user_id=%s %s/%s (%d approvals, 0 edits)",
                user_id,
                connector,
                action_type,
                row["approval_count"],
            )
    except Exception as exc:
        _log.warning(
            "routine_check error for user_id=%s %s/%s: %s",
            user_id,
            connector,
            action_type,
            exc,
        )


def elevate_to_routine(
    *,
    user_id: str,
    connector: str,
    action_type: str,
) -> None:
    """Explicitly elevate an action_type to routine Do for one user."""
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("elevate_to_routine: user_id (keyword-only) is required")
    store = config.get_metadata_store()
    store.execute(
        "INSERT INTO routine_do_tracking "
        "(user_id, connector, action_type, auto_approved, granted_at) "
        "VALUES (%s, %s, %s, TRUE, NOW()) "
        "ON CONFLICT (user_id, connector, action_type) DO UPDATE "
        "SET auto_approved = TRUE, granted_at = NOW(), updated_at = NOW()",
        (user_id, connector, action_type),
    )
    _log.info(
        "routine_elevated user_id=%s connector=%s action_type=%s",
        user_id,
        connector,
        action_type,
    )
