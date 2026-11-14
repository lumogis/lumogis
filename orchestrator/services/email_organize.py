# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-645 — systematic, opt-in mailbox organization (label / folder-sort) + reversal.

Applies Lumogis's internal email ``category`` classification as visible organization
in the user's real mailbox, under two independent per-mailbox opt-ins (default off,
reversible). Reuses the LUM-623 mechanical write layer (``EmailImapSource.apply_organize``
/ ``reverse_writeback``) directly — this is systematic-post-consent, not the per-action
Ask/Do producer. Every write is recorded in the ``email_organize_log`` ledger, which is:
idempotency (a per-op LEFT JOIN excludes done work, includes stragglers), bulk reversal,
and the visible activity log.

Keyed on the signal **url** (``imap://{source_id}/{uidvalidity}/{uid}``) — the email
identity from which ``apply_organize`` parses ``(uidvalidity, uid)``.

Three entry points:
  * ``organize_signal``  — go-forward, best-effort, called post-persist for a live-poll email.
  * ``reconcile_tick``   — a bounded STANDING sweep run every poll tick (retry net + backfill).
  * ``undo_tick``        — a bounded reversal sweep when ``organize_undo_scope`` is set.
"""

from __future__ import annotations

import json
import logging
import time

from actions.errors import PermanentActionError

import config

_log = logging.getLogger(__name__)

try:  # Prefer a typed unique-violation catch; degrade to never-matching if absent.
    from psycopg2.errors import UniqueViolation as _UniqueViolation
except Exception:  # pragma: no cover - psycopg2 is always present in the product image

    class _UniqueViolation(Exception):  # type: ignore[no-redef]
        """Placeholder so the narrow catch below is still syntactically valid."""


_TICK_LIMIT = 200
# LUM-729 follow-up (FP-076). Same number and the same reason as
# `email_cleanup._UNDO_MAX_TICKS`: bound the undoing state so one message that
# cannot be reversed cannot wedge it. Here the consequence of NOT bounding it is
# worse — `feed_monitor._run_organize_tick` skips the reconcile sweep whenever an
# undo reports work, so a permanently-failing row disables organize for that
# mailbox outright, silently, while re-opening an IMAP session every tick forever.
_UNDO_MAX_TICKS = 20
# op -> forward label/move/trash
_LABEL = "label"
_MOVE = "move"
_TRASH = "trash"  # LUM-646: move to the provider \Trash special-use folder (cleanup only)
# Per-source opt-in cache: source_id -> ((label_on, folder_on, folder_slugs, since), expiry).
# A short TTL bounds cross-process staleness: invalidate_opt_in_cache only clears the
# worker that served a PATCH, but the feed poller is a separate process — the TTL lets
# its cache self-heal within a few seconds of an opt-in/undo change (LUM-645 review).
_opt_in_cache: dict[str, tuple] = {}
_OPT_IN_TTL_SECONDS = 20.0


def invalidate_opt_in_cache(source_id: str) -> None:
    _opt_in_cache.pop(str(source_id), None)


def register_hooks() -> None:
    """Subscribe the go-forward organizer to the post-persist email event
    (call once at startup)."""
    import hooks
    from events import Event

    hooks.register(Event.EMAIL_SIGNAL_CLASSIFIED, organize_signal)


def _sanitize_artifact_name(label: str) -> str:
    """Reject control chars (CR/LF/NUL and other C0) and cap length before a
    user-editable taxonomy label is used as a mailbox artifact name."""
    if not label:
        raise PermanentActionError("bad_label", "empty category label")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in label):
        raise PermanentActionError("bad_label", "control character in category label")
    return label[:64]


def _opt_in_for(source_id: str, *, bypass_cache: bool = False) -> tuple:
    key = str(source_id)
    if not bypass_cache:
        hit = _opt_in_cache.get(key)
        if hit is not None and hit[1] > time.monotonic():
            return hit[0]
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: keyed on the source PK (owner-scoped), not a household read.
    row = ms.fetch_one(
        "SELECT organize_label_enabled, organize_folder_enabled, "
        "organize_folder_categories, organize_since "
        "FROM sources WHERE id::text = %s",
        (str(source_id),),
    )
    if not row:
        built = (False, False, frozenset(), None)
    else:
        cats = row.get("organize_folder_categories")
        if isinstance(cats, str):
            try:
                cats = json.loads(cats)
            except ValueError:
                cats = []
        slugs = frozenset(str(c) for c in (cats or []) if c and c != "other")
        built = (
            bool(row.get("organize_label_enabled")),
            bool(row.get("organize_folder_enabled")),
            slugs,
            row.get("organize_since"),
        )
    _opt_in_cache[key] = (built, time.monotonic() + _OPT_IN_TTL_SECONDS)
    return built


def _label_for(user_id: str, slug: str) -> str | None:
    from services import email_taxonomy

    return email_taxonomy.label_map(user_id).get(slug)


def _has_active_row(ms, signal_url: str, op: str) -> bool:
    row = ms.fetch_one(
        "SELECT 1 FROM email_organize_log "
        "WHERE signal_url = %s AND op = %s AND reversed_at IS NULL LIMIT 1",
        (signal_url, op),
    )
    return row is not None


def _apply_one(
    adapter,
    *,
    source_id: str,
    user_id: str,
    signal_url: str,
    op: str,
    label: str,
    slug: str,
    mailbox: str = "INBOX",
    cleanup_job_id: str | None = None,
    client=None,
) -> bool:
    """Apply one organize op and record the ledger row. Returns True on write.
    ``client`` (batch path) reuses one IMAP session across the whole sweep.

    ``mailbox`` is the source folder the op acts on (default INBOX; the LUM-646 spam
    pass passes the provider \\Junk folder). ``cleanup_job_id`` attributes the ledger
    row to a LUM-646 cleanup job (NULL for the standing organize), so a job-scoped
    undo reverses only that job's rows."""
    from actions.handlers.imap_writeback import _parse_imap_url

    parsed = _parse_imap_url(signal_url)
    if parsed is None:
        return False
    _sid, uidvalidity, uid = parsed
    ms = config.get_metadata_store()
    if _has_active_row(ms, signal_url, op):
        return False  # idempotent (partial-unique index is the backstop)
    reverse = adapter.apply_organize(
        uidvalidity=uidvalidity,
        uid=uid,
        op=op,
        label=label,
        slug=slug,
        mailbox=mailbox,
        client=client,
    )
    reverse_token = {"source_id": str(source_id), **reverse}
    try:
        ms.execute(
            "INSERT INTO email_organize_log "
            "(user_id, source_id, signal_url, op, target, label_kind, reverse_token, "
            " cleanup_job_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
            (
                user_id,
                str(source_id),
                signal_url,
                op,
                reverse.get("target"),
                reverse.get("label_kind"),
                json.dumps(reverse_token),
                cleanup_job_id,
            ),
        )
    except _UniqueViolation:
        # The ONLY benign failure: a concurrent tick won the race on
        # `uq_email_organize_active`, so the row already exists and the work is
        # already recorded.
        _log.info("email.organize.duplicate_row signal_url=%s op=%s", signal_url, op)
    except Exception:
        # Anything else is NOT benign and must not be silently relabelled as a
        # duplicate. The mailbox write has already been applied at this point, so
        # a missing ledger row means no undo path AND re-application on every
        # subsequent tick, forever. This catch used to be bare, and it hid exactly
        # that: a schema drift (`cleanup_job_id` absent) surfaced for years as
        # `duplicate_row` at INFO. Re-raised so the caller's own error handling
        # sees a real failure.
        _log.exception("email.organize.ledger_write_failed signal_url=%s op=%s", signal_url, op)
        raise
    return True


def _adapter_for(source_id: str, user_id: str):
    from actions.handlers.imap_writeback import _load_owned_source
    from adapters.email_imap_source import EmailImapSource

    return EmailImapSource(_load_owned_source(source_id, user_id))


def organize_signal(*, signal_url: str, source_id: str, category: str, user_id: str) -> None:
    """Go-forward: organize one freshly-classified live-poll email. Best-effort —
    never raises to the caller (fired in the background)."""
    try:
        label_on, folder_on, folder_slugs, _since = _opt_in_for(source_id)
        if not (label_on or folder_on):
            return
        if not category:
            return
        label = _label_for(user_id, category)
        if not label:  # since-deleted custom slug — skip, don't KeyError
            return
        label = _sanitize_artifact_name(label)
        adapter = _adapter_for(source_id, user_id)
        client = adapter.organize_session()  # one session for this message's ops
        try:
            # Label BEFORE move (label while still in INBOX; survives the COPY).
            if label_on:
                _safe_apply(
                    adapter,
                    source_id=source_id,
                    user_id=user_id,
                    signal_url=signal_url,
                    op=_LABEL,
                    label=label,
                    slug=category,
                    client=client,
                )
            if folder_on and category != "other" and category in folder_slugs:
                _safe_apply(
                    adapter,
                    source_id=source_id,
                    user_id=user_id,
                    signal_url=signal_url,
                    op=_MOVE,
                    label=label,
                    slug=category,
                    client=client,
                )
        finally:
            adapter.close_session(client)
    except Exception as exc:  # noqa: BLE001 - background best-effort
        _log.warning(
            "email.organize.go_forward_failed url=%s type=%s", signal_url, type(exc).__name__
        )


def _safe_apply(adapter, **kw) -> bool:
    """Apply one op, swallowing per-message failures so the batch/hook continues.
    Returns True iff a ledger row was written. A transient failure writes no ledger
    row (returns False) → retried by the next reconcile_tick."""
    try:
        return _apply_one(adapter, **kw)
    except PermanentActionError as exc:
        _log.info(
            "email.organize.permanent_skip url=%s op=%s reason=%s",
            kw.get("signal_url"),
            kw.get("op"),
            exc.code,
        )
    except Exception as exc:  # noqa: BLE001 - transient; no ledger row, retried later
        _log.warning(
            "email.organize.apply_failed url=%s op=%s type=%s",
            kw.get("signal_url"),
            kw.get("op"),
            type(exc).__name__,
        )
    return False


def _pending_for_op(
    ms, *, source_id: str, op: str, since, limit: int, categories: list | None = None
) -> list[dict]:
    # signals.source_id is TEXT (= str(sources.id)). Per-op LEFT JOIN: a signal with a
    # label row but no move row is still selected for move. created_at >= organize_since
    # scopes to go-forward by default (apply-to-existing widens `since` to epoch).
    # `categories` (folder-sort) filters IN SQL so out-of-scope mail never fills the LIMIT
    # window — otherwise the oldest non-selected emails would permanently starve the
    # selected ones past the window (LUM-645 review).
    # SCOPE-EXEMPT: owner's own source_id + created watermark, not a household read.
    cat_clause = " AND s.category = ANY(%s)" if categories is not None else ""
    params = [op, str(source_id), since]
    if categories is not None:
        params.append(list(categories))
    params.append(limit)
    return (
        ms.fetch_all(
            "SELECT s.url AS url, s.category AS category FROM signals s "
            "LEFT JOIN email_organize_log eol "
            "  ON eol.signal_url = s.url AND eol.op = %s AND eol.reversed_at IS NULL "
            "WHERE s.source_id = %s AND s.category <> '' AND s.created_at >= %s "
            f"  AND eol.id IS NULL{cat_clause} "
            "ORDER BY s.created_at LIMIT %s",
            tuple(params),
        )
        or []
    )


def reconcile_tick(*, source_id: str, user_id: str, limit: int = _TICK_LIMIT) -> dict:
    """One bounded standing sweep: apply pending label/move ops to classified signals
    lacking an active ledger row (created since the opt-in watermark). Run every poll
    tick, so a transiently-failed go-forward write is retried here."""
    label_on, folder_on, folder_slugs, since = _opt_in_for(source_id)
    result = {"scanned": 0, "labeled": 0, "moved": 0, "skipped": 0, "failed": 0}
    if not (label_on or folder_on) or since is None:
        return result
    ms = config.get_metadata_store()
    # One IMAP session for the whole tick (opened lazily on first pending row) so the
    # sweep does not connect+login+logout per message — a real provider lockout risk.
    state = {"adapter": None, "client": None}

    def _session():
        if state["adapter"] is None:
            state["adapter"] = _adapter_for(source_id, user_id)
            state["client"] = state["adapter"].organize_session()
        return state["adapter"], state["client"]

    try:
        if label_on:
            for row in _pending_for_op(
                ms, source_id=source_id, op=_LABEL, since=since, limit=limit
            ):
                result["scanned"] += 1
                label = _label_for(user_id, row["category"])
                if not label:
                    result["skipped"] += 1
                    continue
                adapter, client = _session()
                applied = _safe_apply(
                    adapter,
                    source_id=source_id,
                    user_id=user_id,
                    signal_url=row["url"],
                    op=_LABEL,
                    label=_sanitize_artifact_name(label),
                    slug=row["category"],
                    client=client,
                )
                result["labeled" if applied else "failed"] += 1
        if folder_on and folder_slugs:
            # Filter selected categories IN SQL so out-of-scope mail can't starve the window.
            for row in _pending_for_op(
                ms,
                source_id=source_id,
                op=_MOVE,
                since=since,
                limit=limit,
                categories=list(folder_slugs),
            ):
                result["scanned"] += 1
                label = _label_for(user_id, row["category"])
                if not label:
                    result["skipped"] += 1
                    continue
                adapter, client = _session()
                applied = _safe_apply(
                    adapter,
                    source_id=source_id,
                    user_id=user_id,
                    signal_url=row["url"],
                    op=_MOVE,
                    label=_sanitize_artifact_name(label),
                    slug=row["category"],
                    client=client,
                )
                result["moved" if applied else "failed"] += 1
    finally:
        if state["client"] is not None:
            state["adapter"].close_session(state["client"])
    return result


def undo_tick(*, source_id: str, user_id: str, limit: int = _TICK_LIMIT) -> dict:
    """One bounded reversal sweep when organize_undo_scope is set. Reverses move rows
    before label rows per signal_url (a label reverse searches the original mailbox by
    Message-ID, which fails if the message was moved out of INBOX first). Clears
    organize_undo_scope when no active rows remain for the scope."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT organize_undo_scope, COALESCE(organize_undo_ticks, 0) AS ticks "
        "FROM sources WHERE id::text = %s",
        (str(source_id),),
    )
    scope = (row or {}).get("organize_undo_scope")
    result = {"reversed": 0, "failed": 0, "remaining": 0, "gave_up": False}
    if not scope:
        return result
    ops = {_LABEL, _MOVE} if scope == "all" else ({_MOVE} if scope == "folder" else {_LABEL})
    # move before label; oldest first for stability.
    rows = (
        ms.fetch_all(
            "SELECT id, signal_url, op, reverse_token FROM email_organize_log "
            "WHERE source_id = %s AND reversed_at IS NULL AND op = ANY(%s) "
            "ORDER BY (op = 'move') DESC, id ASC LIMIT %s",
            (str(source_id), list(ops), limit),
        )
        or []
    )
    if not rows:
        _finish_undo(ms, source_id)
        return result
    adapter = _adapter_for(source_id, user_id)
    client = adapter.organize_session()  # one session for the reversal batch
    try:
        for r in rows:
            token = r["reverse_token"] if isinstance(r["reverse_token"], dict) else {}
            try:
                adapter.reverse_writeback(
                    op=token.get("op"),
                    mailbox=token.get("mailbox", "INBOX"),
                    message_id=token.get("message_id"),
                    target=token.get("target"),
                    flag=token.get("flag"),
                    client=client,
                )
                ms.execute(
                    "UPDATE email_organize_log SET reversed_at = NOW() WHERE id = %s", (r["id"],)
                )
                result["reversed"] += 1
            except Exception as exc:  # noqa: BLE001 - counted, row stays active, retried next tick
                _log.warning(
                    "email.organize.reverse_failed url=%s type=%s",
                    r["signal_url"],
                    type(exc).__name__,
                )
                result["failed"] += 1
    finally:
        adapter.close_session(client)
    remaining = ms.fetch_one(
        "SELECT 1 FROM email_organize_log WHERE source_id = %s AND reversed_at IS NULL "
        "AND op = ANY(%s) LIMIT 1",
        (str(source_id), list(ops)),
    )
    if remaining is None:
        _finish_undo(ms, source_id)
        return result

    # LUM-729 follow-up (FP-076). Rows remain, so count this sweep against the
    # bound. Without it a message that can never be reversed keeps the scope set
    # forever — and because `_run_organize_tick` skips the reconcile sweep while
    # an undo reports work, that permanently disables organize for the mailbox
    # while re-opening an IMAP session every tick to retry the same doomed row.
    ticks = int((row or {}).get("ticks") or 0) + 1
    if ticks >= _UNDO_MAX_TICKS:
        unreversed = _unreversed_count(ms, source_id, ops)
        # Give up LOUDLY and VISIBLY. `organize_undo_gave_up_at` is read by the
        # log route, because a silent give-up would trade one invisible failure
        # for another — which is the whole complaint LUM-729 was about.
        ms.execute(
            "UPDATE sources SET organize_undo_scope = NULL, organize_undo_ticks = 0, "
            "organize_undo_gave_up_at = NOW() WHERE id::text = %s",
            (str(source_id),),
        )
        _log.warning(
            "email.organize.undo_gave_up source_id=%s scope=%s ticks=%d unreversed=%d "
            "— organize resumes; these messages stay where Lumogis put them",
            source_id,
            scope,
            ticks,
            unreversed,
        )
        result["gave_up"] = True
        return result

    ms.execute(
        "UPDATE sources SET organize_undo_ticks = %s WHERE id::text = %s",
        (ticks, str(source_id)),
    )
    result["remaining"] = 1
    return result


def _unreversed_count(ms, source_id: str, ops: set) -> int:
    """How many rows the undo is abandoning. Counted only on the give-up path —
    it is for the WARNING, and paying for it on every tick would be waste."""
    try:
        row = ms.fetch_one(
            "SELECT COUNT(*) AS n FROM email_organize_log "
            "WHERE source_id = %s AND reversed_at IS NULL AND op = ANY(%s)",
            (str(source_id), list(ops)),
        )
        return int((row or {}).get("n") or 0)
    except Exception:  # noqa: BLE001 - a diagnostic must never break the give-up
        return -1


def _finish_undo(ms, source_id: str) -> None:
    """The undo completed. Clear the scope, reset the tick counter so the NEXT
    undo gets a full budget, and clear any previous give-up stamp — a successful
    undo means the stale warning must not keep showing."""
    ms.execute(
        "UPDATE sources SET organize_undo_scope = NULL, organize_undo_ticks = 0, "
        "organize_undo_gave_up_at = NULL WHERE id::text = %s",
        (str(source_id),),
    )
