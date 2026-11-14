# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-(user, signal) triage state — the spine of the LUM-641 email arc (LUM-643).

Durable, Lumogis-owned status on each signal (``needs_you`` / ``good_to_know`` /
``snoozed`` / ``done``), decoupled from the mailbox's read-state — *the mailbox
forgets; Lumogis remembers*. Reading never changes state; the user defers/completes
explicitly. Snooze re-surfaces at **query time** (``snoozed_until <= now()`` →
treated as ``needs_you`` by the web layer); there is no scheduler.

Triage is **per-user**: the PK ``(user_id, signal_id)`` lets each household member
triage a shared (projected) signal independently. All reads/writes bind the
caller's ``user_id`` — one member's triage is never visible to or mutable by
another.

This module owns the parameterised SQL only:

* :data:`VALID_STATUSES` — the allowed status set.
* :func:`build_signals_query` — the ``GET /signals`` SELECT with the triage
  **LATERAL** join, shared with ``routes.signals.list_signals`` so a real-Postgres
  test can execute the *exact* SQL the route runs (a plain ``LEFT JOIN
  signal_triage`` would throw ``42702 ambiguous column`` — both tables carry
  ``user_id``/``signal_id`` and the SELECT + ``visible_filter`` use unqualified
  columns; the LATERAL exposes only the two new columns so ``signals`` stays
  unambiguous and ``visible_filter`` is genuinely untouched).
* :func:`set_triage` / :func:`clear_triage` — the UPSERT/DELETE behind the route.

Reminder *delivery* (the snooze-expiry ping / morning brief) is **LUM-648**, not
here.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import config

try:  # Prefer a typed FK-violation catch; fall back to message-matching if absent.
    from psycopg2.errors import ForeignKeyViolation as _ForeignKeyViolation
except Exception:  # pragma: no cover - psycopg2 always present in the product image
    _ForeignKeyViolation = ()

VALID_STATUSES: frozenset[str] = frozenset({"needs_you", "good_to_know", "snoozed", "done"})

# Columns selected from ``signals`` (all unqualified — they belong to ``signals``;
# ``signal_triage`` contributes ONLY the two aliased columns from the LATERAL, so
# there is no ambiguity). ``published_from`` is additive (LUM-643) for a future
# owner/projection dedupe follow-up.
_SIGNALS_COLUMNS = (
    "signal_id, user_id, source_id, title, url, published_at, content_summary, "
    "entities, topics, importance_score, relevance_score, notified, created_at, "
    "category, from_addr, from_display, scope, source_url, source_label, published_from"
)

# The per-caller triage LATERAL. Its single ``%s`` (the caller's user_id) is the
# FIRST placeholder in the assembled SQL — callers MUST prepend it to ``params``
# before the ``visible_filter`` / other predicates, never append. (Exception:
# when ``build_signals_query`` is given a non-empty ``extra_select``, that
# clause's placeholders come first, because SELECT emits before FROM. See its
# docstring for the full order.)
#
# signal_triage is a scope-less, per-user OWNED table; signal visibility is
# enforced separately by ``visible_filter`` in routes/signals.py before any
# triage row is joined.
_TRIAGE_LATERAL = (
    "LEFT JOIN LATERAL ("
    "SELECT st.status AS triage_status, st.snoozed_until, st.updated_at AS triage_updated_at "
    "FROM signal_triage st "
    # SCOPE-EXEMPT: per-user owned table; user_id binds the caller's own rows.
    "WHERE st.user_id = %s AND st.signal_id = signals.signal_id"
    ") t ON TRUE"
)


def build_signals_query(
    where: str,
    order_by: str,
    *,
    extra_join: str = "",
    extra_select: str = "",
) -> str:
    """Return the full ``GET /signals`` SELECT with the triage LATERAL.

    ``where`` is the already-composed ``AND``-joined predicate string (the caller
    owns parameter binding and ordering of ``%s`` placeholders); ``order_by`` is a
    trusted, non-user column expression (``created_at DESC`` for the recency window
    or ``t.triage_updated_at DESC`` for the ``triaged=set`` fetch). A trailing
    ``LIMIT %s`` is always appended.

    Shared with :func:`routes.signals.list_signals` so tests execute the exact SQL.

    ``extra_join`` / ``extra_select`` widen this for email search (LUM-819),
    which needs a second LATERAL for the stored message text. Both are trusted,
    caller-authored SQL — never user input.

    **Placeholder order, stated exactly, because getting it wrong binds the
    caller's id to the wrong ``%s`` and authorises the query as another user:**

    1. ``extra_select`` — it sits in the SELECT list, which emits BEFORE ``FROM``
    2. the triage LATERAL's ``user_id``
    3. ``extra_join``'s, in the order they appear
    4. ``where``'s
    5. ``order_by``'s
    6. the trailing ``LIMIT``

    Note this means the triage LATERAL is only the first placeholder when
    ``extra_select`` is empty — which is why the note on ``_TRIAGE_LATERAL``
    says "prepend" for the plain ``GET /signals`` case.
    """
    select_extra = f", {extra_select}" if extra_select else ""
    join_extra = f" {extra_join}" if extra_join else ""
    return (
        f"SELECT {_SIGNALS_COLUMNS}, t.triage_status, t.snoozed_until, "
        f"t.triage_updated_at{select_extra} "
        f"FROM signals {_TRIAGE_LATERAL}{join_extra} "
        f"WHERE {where} ORDER BY {order_by} LIMIT %s"
    )


class TriageValidationError(ValueError):
    """Bad status, or ``snoozed`` without a strictly-future, TZ-aware ``snoozed_until``."""


class TriageSignalMissing(Exception):
    """The signal vanished between the visibility guard and the UPSERT (FK race)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate(status: str, snoozed_until: datetime | None) -> datetime | None:
    """Validate the (status, snoozed_until) pair; return the normalised value.

    Non-snoozed statuses null ``snoozed_until``. ``snoozed`` requires a TZ-aware,
    strictly-future timestamp (a naive value would raise ``TypeError`` when compared
    against an aware ``now`` — reject it up front as invalid, not 500).
    """
    if status not in VALID_STATUSES:
        raise TriageValidationError(f"invalid status {status!r}")
    if status != "snoozed":
        return None
    if snoozed_until is None:
        raise TriageValidationError("snoozed requires snoozed_until")
    if snoozed_until.tzinfo is None or snoozed_until.utcoffset() is None:
        raise TriageValidationError("snoozed_until must be timezone-aware")
    if snoozed_until <= _now():
        raise TriageValidationError("snoozed_until must be in the future")
    return snoozed_until


def set_triage(
    user_id: str,
    signal_id: str,
    status: str,
    snoozed_until: datetime | None,
) -> dict:
    """UPSERT the caller's triage row for ``signal_id``; return the stored row.

    Raises :class:`TriageValidationError` (→ 422) on a bad status / snooze pairing,
    and :class:`TriageSignalMissing` (→ 404) if the signal FK is violated by a
    concurrent delete. The caller (route) is responsible for the visibility guard
    *before* this runs.
    """
    normalised = _validate(status, snoozed_until)
    store = config.get_metadata_store()
    try:
        row = store.fetch_one(
            "INSERT INTO signal_triage (user_id, signal_id, status, snoozed_until, updated_at) "
            "VALUES (%s, %s, %s, %s, NOW()) "
            "ON CONFLICT (user_id, signal_id) DO UPDATE SET "
            "status = EXCLUDED.status, snoozed_until = EXCLUDED.snoozed_until, updated_at = NOW() "
            "RETURNING signal_id, status, snoozed_until, updated_at",
            (user_id, str(signal_id), status, normalised),
        )
    except Exception as exc:  # noqa: BLE001 — re-raise non-FK failures untouched
        # Prefer the typed ForeignKeyViolation (robust to localized/reworded error
        # text); fall back to message-matching so a store that wraps the psycopg2
        # error in a plain Exception is still classified (LUM-660).
        msg = str(exc).lower()
        is_fk = (_ForeignKeyViolation and isinstance(exc, _ForeignKeyViolation)) or (
            "foreign key" in msg or "signal_triage_signal_id_fkey" in msg
        )
        if is_fk:
            raise TriageSignalMissing(str(exc)) from exc
        raise
    if row is None:
        # RETURNING always yields a row on success; a None here means the write did
        # not land (should not happen) — surface as a missing signal rather than 500.
        raise TriageSignalMissing("triage upsert returned no row")
    return row


def clear_triage(user_id: str, signal_id: str) -> None:
    """Delete the caller's triage row for ``signal_id`` (idempotent).

    No-op if there is no row → the signal falls back to default lane derivation.
    The caller (route) performs the visibility guard before this runs.
    """
    store = config.get_metadata_store()
    # SCOPE-EXEMPT: per-user OWNED table (no scope column); user_id binds the
    # caller's own triage row. Visibility of the signal is guarded by the route.
    store.execute(
        "DELETE FROM signal_triage WHERE user_id = %s AND signal_id = %s",
        (user_id, str(signal_id)),
    )


# ---------------------------------------------------------------------------
# Bulk triage (LUM-696)
# ---------------------------------------------------------------------------
#
# The two functions below are the bulk siblings of ``set_triage`` /
# ``clear_triage``. Three things about them are deliberate and are the whole
# reason they are not loops over the singles:
#
# 1. **Visibility lives INSIDE the write.** The single-signal path is
#    check-then-write (``routes._signal_visible_to`` then ``set_triage``), which
#    has a real race — the signal can be deleted in between, which is exactly
#    why :class:`TriageSignalMissing` exists and is mapped to 404. Sourcing the
#    rows from ``signals`` in the same statement closes that window: a row that
#    is not visible is not selected, and a row that does not exist cannot be
#    selected, so the FK cannot be violated. A batch of 200 must not fail
#    wholesale because one signal was purged mid-flight.
#
# 2. **The visible rows are resolved in a CTE, not a join.** ``visible_filter``
#    emits *unqualified* ``user_id`` and ``scope``. A ``DELETE … USING signals``
#    would put both ``signal_triage`` and ``signals`` in one scope and both carry
#    ``user_id`` → ``42702 ambiguous_column`` at runtime, invisible to every
#    fake-store test because the house fake string-matches SQL and cannot execute
#    it. This is the same trap the module docstring already documents for the
#    ``build_signals_query`` LATERAL. A CTE cannot see the outer statement's
#    tables, so ``{visible_clause}`` is always evaluated against ``signals``
#    alone.
#
# 3. **Both statements return the VISIBLE set, never the write's RETURNING.**
#    ``DELETE … RETURNING`` yields only ids that had a row to delete, so a signal
#    that is visible but was never triaged — the ordinary state of most of an
#    inbox — would come back empty and be reported to the caller as "gone". That
#    would contradict ``clear_triage``'s documented idempotence one function
#    above. Selecting from the ``visible`` CTE makes
#    ``skipped = requested - visible`` mean the same thing in both statements:
#    *you asked about it and you cannot see it*.

# API contract, not a tuning knob: the web client chunks against this number.
# Lowering it 422s existing clients; raising it is safe. Mirrored in
# ``clients/lumogis-web/src/api/emailSources.ts`` as ``MAX_BULK_TRIAGE``.
MAX_BULK_SIGNALS = 200


def set_triage_bulk(
    user_id: str,
    signal_ids: list[str],
    status: str,
    snoozed_until: datetime | None,
    *,
    visible_clause: str,
    visible_params: tuple,
) -> list[dict]:
    """UPSERT ``status`` onto every signal in ``signal_ids`` the caller can see.

    Returns one dict per **visible** id (``signal_id``, ``status``,
    ``snoozed_until``, ``updated_at``); ids absent from the result were not
    visible to the caller. Raises :class:`TriageValidationError` (→ 422) on a bad
    status / snooze pairing — validated **once** for the batch, since one status
    applies to all of it.

    ``ON CONFLICT DO UPDATE`` is required (never ``DO NOTHING``): an id that
    already has a triage row must come back in the result with its **new**
    status, not be silently dropped.

    Placeholder order — CTE binds first::

        (signal_ids, *visible_params, user_id, status, snoozed_until)

    (The module's other two triage queries bind in the OPPOSITE order on purpose;
    copy-paste between them is the bug to fear.)
    """
    normalised = _validate(status, snoozed_until)
    ids = _dedupe_ids(signal_ids)
    if not ids:
        return []
    store = config.get_metadata_store()
    sql = (
        "WITH visible AS ("
        "SELECT s.signal_id FROM signals s "
        f"WHERE s.signal_id = ANY(%s::uuid[]) AND {visible_clause}"
        "), upserted AS ("
        "INSERT INTO signal_triage (user_id, signal_id, status, snoozed_until, updated_at) "
        "SELECT %s, signal_id, %s, %s, NOW() FROM visible "
        "ON CONFLICT (user_id, signal_id) DO UPDATE SET "
        "status = EXCLUDED.status, snoozed_until = EXCLUDED.snoozed_until, updated_at = NOW() "
        "RETURNING signal_id, status, snoozed_until, updated_at"
        ") "
        "SELECT v.signal_id, u.status, u.snoozed_until, u.updated_at "
        "FROM visible v LEFT JOIN upserted u ON u.signal_id = v.signal_id"
    )
    rows = store.fetch_all(sql, (ids, *visible_params, user_id, status, normalised))
    return list(rows or [])


def clear_triage_bulk(
    user_id: str,
    signal_ids: list[str],
    *,
    visible_clause: str,
    visible_params: tuple,
) -> list[dict]:
    """Delete the caller's triage rows for every visible id in ``signal_ids``.

    Idempotent, exactly like :func:`clear_triage`: an id that is visible but was
    never triaged is a **success** (it comes back in the result with null
    columns), not a miss. Ids absent from the result were not visible.

    The ``deleted`` CTE is data-modifying, so Postgres executes it exactly once
    and to completion whether or not the final ``SELECT`` reads its rows — the
    delete happens even though the result set comes from ``visible``.

    Placeholder order — CTE binds first::

        (signal_ids, *visible_params, user_id)
    """
    ids = _dedupe_ids(signal_ids)
    if not ids:
        return []
    store = config.get_metadata_store()
    sql = (
        "WITH visible AS ("
        "SELECT s.signal_id FROM signals s "
        f"WHERE s.signal_id = ANY(%s::uuid[]) AND {visible_clause}"
        "), deleted AS ("
        # SCOPE-EXEMPT: per-user OWNED table (no scope column); user_id binds the
        # caller's own triage rows. Signal visibility is the `visible` CTE above.
        "DELETE FROM signal_triage WHERE user_id = %s "
        "AND signal_id IN (SELECT signal_id FROM visible) "
        "RETURNING signal_id"
        ") "
        "SELECT signal_id FROM visible"
    )
    rows = store.fetch_all(sql, (ids, *visible_params, user_id))
    return list(rows or [])


def _dedupe_ids(signal_ids: list[str]) -> list[str]:
    """Stringified ids, first occurrence order, duplicates removed.

    Duplicates must not survive: ``ON CONFLICT`` makes them harmless to the
    write, but the route's ``skipped = requested - returned`` arithmetic would
    otherwise report the second copy of an id as skipped. Stringified because
    ``= ANY(%s::uuid[])`` is bound a ``list[str]`` throughout this codebase
    (``services/document_purge.py``), not a list of ``uuid.UUID``.
    """
    seen: set[str] = set()
    out: list[str] = []
    for sid in signal_ids:
        s = str(sid)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
