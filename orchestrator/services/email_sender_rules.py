# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Standing per-sender suppression (LUM-697).

**v1 supports exactly one action: ``hide``** — the message is never surfaced,
and **no IMAP operation is performed**. Nothing is written to the mailbox,
nothing to ``email_organize_log``, and no adapter session is opened.

``mark_read`` and ``trash`` were cut (LUM-709), not forgotten. Three things were
verified during review:

* ``email_organize._apply_one`` routes to ``adapter.apply_organize``, whose op
  set is **label / move / trash only** — anything else raises ``unknown_op``
  (``email_imap_source.py:695``). ``mark_read`` lives on ``apply_writeback``, a
  different method with a different reverse-token shape, so every ``mark_read``
  rule would have raised and been swallowed silently, forever.
* ``reconcile_tick`` finds pending work via ``_pending_for_op``, a LEFT JOIN
  over **``signals``**. A suppressed message is deliberately never persisted, so
  no retry sweep can ever reach it.
* ``undo_tick`` reverses only ``{label, move}``, so a suppression ``trash`` row
  would have been reachable by no undo path at all.

``hide`` needs none of that machinery because it touches nothing.

**Failure posture: fails OPEN.** Any error here surfaces the message. A bug that
hides mail is worse than a bug that shows it.
"""

from __future__ import annotations

import logging
import time

import config

_log = logging.getLogger(__name__)

ACTIONS = ("hide",)

# Consulted once per ingested message, so it must not be a DB round trip each
# time. Mirrors email_organize._OPT_IN_TTL_SECONDS. Invalidated on write, so the
# TTL only matters for a rule set in another worker process — the same accepted
# limitation the organize opt-in cache already carries.
_CACHE_TTL_SECONDS = 20.0
_cache: dict[str, tuple[float, set[tuple[str, str]]]] = {}


def _cache_key(user_id: str) -> str:
    return user_id


def invalidate_cache(user_id: str | None = None) -> None:
    """Drop cached rules so a write is visible immediately, not after the TTL."""
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(_cache_key(user_id), None)


def _active_rules(user_id: str) -> set[tuple[str, str]]:
    key = _cache_key(user_id)
    hit = _cache.get(key)
    if hit is not None and (time.monotonic() - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: the caller's own suppression rules, not a household read.
    rows = (
        ms.fetch_all(
            "SELECT source_id::text AS source_id, from_addr FROM email_sender_rule "
            "WHERE user_id = %s AND active",
            (user_id,),
        )
        or []
    )
    pairs = {(str(r["source_id"]), str(r["from_addr"]).lower()) for r in rows}
    _cache[key] = (time.monotonic(), pairs)
    return pairs


def should_suppress(user_id: str, source_id: str, from_addr: str) -> bool:
    """True when this sender is silenced for this mailbox. Never raises."""
    try:
        if not (from_addr or "").strip():
            return False
        return (str(source_id), from_addr.strip().lower()) in _active_rules(user_id)
    except Exception:  # noqa: BLE001 - FAIL OPEN. Showing mail we meant to hide
        # is recoverable; hiding mail because the store blipped is not.
        _log.warning("sender_rules: should_suppress degraded to False (showing message)")
        return False


def set_rule(user_id: str, source_id: str, from_addr: str, action: str = "hide") -> dict:
    """Create or re-activate a suppression rule. Raises ValueError on a bad action."""
    if action not in ACTIONS:
        raise ValueError(f"unsupported suppression action: {action!r}")
    ms = config.get_metadata_store()
    ms.execute(
        "INSERT INTO email_sender_rule (user_id, source_id, from_addr, action, active) "
        "VALUES (%s, %s::uuid, %s, %s, TRUE) "
        "ON CONFLICT (user_id, source_id, from_addr) DO UPDATE SET "
        "  action = EXCLUDED.action, active = TRUE",
        (user_id, str(source_id), from_addr.strip().lower(), action),
    )
    invalidate_cache(user_id)
    return {"status": "set", "action": action}


def clear_rule(user_id: str, source_id: str, from_addr: str) -> int:
    """Remove a rule. Returns the number of rows removed (0 → 404 at the route).

    **Go-forward only.** A hidden message has no ``signals`` row and the IMAP
    poll cursor has advanced past its UID, so clearing a rule cannot resurface
    anything already hidden — only the LUM-711 backfill could. The UI must say
    "stop hiding future mail", not "restore".
    """
    ms = config.get_metadata_store()
    rows = (
        ms.fetch_all(
            "DELETE FROM email_sender_rule "
            # SCOPE-EXEMPT: caller's own suppression rule, keyed on their own user_id.
            "WHERE user_id = %s AND source_id = %s::uuid AND from_addr = %s RETURNING id",
            (user_id, str(source_id), from_addr.strip().lower()),
        )
        or []
    )
    invalidate_cache(user_id)
    return len(rows)


def list_rules(user_id: str, *, source_id: str | None = None) -> list[dict]:
    """The caller's own rules. Never raises."""
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: the caller's own suppression rules, not a household read.
        sql = (
            "SELECT source_id::text AS source_id, from_addr, action, active, created_at "
            "FROM email_sender_rule WHERE user_id = %s"
        )
        params: list = [user_id]
        if source_id:
            sql += " AND source_id = %s::uuid"
            params.append(str(source_id))
        sql += " ORDER BY created_at DESC"
        return ms.fetch_all(sql, tuple(params)) or []
    except Exception:  # noqa: BLE001
        _log.warning("sender_rules: list_rules degraded to empty")
        return []
