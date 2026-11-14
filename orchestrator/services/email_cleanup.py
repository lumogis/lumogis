# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-646 — chunked inbox cleanup: date-range passes + a separate "empty spam to Trash".

On-demand, per-mailbox, re-runnable. Two job kinds (``email_cleanup_job.kind``):

* ``window`` — process a custom ``[window_start, window_end]`` date range: backfill+classify
  it into signals (reusing the LUM-644 ``backfill_slice``), auto-label (label-visible
  providers only, and only when the standing LUM-645 label-organize is OFF), and execute
  user-approved *move-to-Lumogis-folder* actions. **Never trashes INBOX mail** (bulk ≠ spam,
  ADR LUM-647).
* ``spam`` — empty the provider's ``\\Junk`` special-use folder to ``\\Trash`` (the only
  destructive path; provider-confirmed spam; reversible).

Execution + reversal ride the LUM-645 ``email_organize_log`` ledger, but every cleanup-written
row carries this job's id in ``cleanup_job_id`` so a **job-scoped** undo reverses only this
job's rows — never a row the standing organize owns. Heavy IMAP work runs on the feed tick
(``run_cleanup_tick``); routes only enqueue.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

import config
from services import email_organize as eo

_log = logging.getLogger(__name__)

_TICK_LIMIT = 200
_UNDO_MAX_TICKS = 20  # bound the 'undoing' state so an irreversible message can't wedge a job
_EPOCH = date(1970, 1, 1)
_ACTIVE_STATES = ("pending", "backfilling", "ready", "executing", "undoing")


class CleanupError(Exception):
    """Raised for caller-facing conditions (mapped to HTTP status by the route)."""

    def __init__(self, code: str, status: int = 409, detail: Optional[dict] = None):
        super().__init__(code)
        self.code = code
        self.status = status
        # LUM-729: a refusal the user is expected to ACT on needs to say what
        # will happen, not just name itself. `detail` carries the full body when
        # there is one; the route falls back to `{"code": ...}` for the rest,
        # which are diagnostics rather than decisions.
        self.detail = detail


# ---- helpers --------------------------------------------------------------


def _synthetic_url(source_id: str, uidvalidity: int, uid: int) -> str:
    return f"imap://{source_id}/{uidvalidity}/{uid}"


def _uid_from_url(url: str) -> int | None:
    from actions.handlers.imap_writeback import _parse_imap_url

    parsed = _parse_imap_url(url)
    return parsed[2] if parsed else None


def _active_job(ms, source_id: str) -> dict | None:
    return ms.fetch_one(
        "SELECT * FROM email_cleanup_job WHERE source_id = %s "
        "AND state = ANY(%s) ORDER BY created_at DESC LIMIT 1",
        (str(source_id), list(_ACTIVE_STATES)),
    )


def cancel_active_jobs_for_source(source_id: str, user_id: str) -> int:
    """Force any non-terminal job for ``source_id`` to ``failed`` (LUM-701).

    ``email_cleanup_job.source_id`` carries no FK to ``sources`` (migration
    061), so removing the mailbox would otherwise leave an active job ticking
    forever against a row that no longer exists — every tick's
    ``eo._adapter_for`` raises ``PermanentActionError``, which the feed-monitor
    poll wrapper swallows silently. Call this from the mailbox-delete route
    before the ``sources`` row is removed. Returns the number of jobs cancelled.
    """
    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        "UPDATE email_cleanup_job SET state = 'failed', updated_at = NOW() "
        "WHERE source_id = %s AND user_id = %s AND state = ANY(%s) "
        "RETURNING id",
        (str(source_id), user_id, list(_ACTIVE_STATES)),
    )
    return len(rows or [])


def _owned_job(ms, job_id: str, user_id: str, source_id: str) -> dict | None:
    # A job UUID alone never authorizes: re-check user_id + source_id (the route already
    # resolved+authorized source_id, this guards a guessed job UUID crossing users/sources).
    return ms.fetch_one(
        "SELECT * FROM email_cleanup_job WHERE id = %s AND user_id = %s AND source_id = %s",
        (job_id, user_id, str(source_id)),
    )


def _label_visible(source) -> bool:
    from urllib.parse import urlparse

    from services import email_providers

    host = urlparse(getattr(source, "url", "") or "").hostname or ""
    return email_providers.label_visible_for_host(host)


def _standing_label_on(ms, source_id: str) -> bool:
    row = ms.fetch_one(
        "SELECT organize_label_enabled FROM sources WHERE id::text = %s", (str(source_id),)
    )
    return bool(row and row.get("organize_label_enabled"))


def _is_duplicate(ms, url: str, user_id: str) -> bool:
    return (
        ms.fetch_one(
            "SELECT 1 FROM signals WHERE url = %s AND user_id = %s LIMIT 1", (url, user_id)
        )
        is not None
    )


def _set_state(ms, job_id, state: str) -> None:
    ms.execute(
        "UPDATE email_cleanup_job SET state = %s, updated_at = NOW() WHERE id = %s",
        (state, job_id),
    )


def _seed_folder(source_id: str, user_id: str, role: str | None) -> tuple[str | None, int, int]:
    """Open a short read session and return ``(folder, uidvalidity, max_uid)``. ``role``
    None → INBOX (``folder`` is 'INBOX'); otherwise resolve the SPECIAL-USE role folder
    (``folder`` None if the server advertises none)."""
    adapter = eo._adapter_for(source_id, user_id)
    if role is None:
        uidvalidity, max_uid = adapter.inbox_seed()
        return "INBOX", uidvalidity, max_uid
    client = adapter.organize_session()
    try:
        folder = adapter._role_folder(client, role)
        if folder is None:
            return None, 0, 0
        uidvalidity, uidnext = adapter.folder_uidnext(client, folder)
        return folder, uidvalidity, max(uidnext - 1, 0)
    finally:
        adapter.close_session(client)


# ---- start / approve / undo (route-facing, no heavy IMAP on the request path) ----


def start_job(source_id: str, user_id: str, window_start, window_end) -> dict:
    ms = config.get_metadata_store()
    if _active_job(ms, source_id) is not None:
        raise CleanupError("cleanup_active", 409)
    _folder, uidvalidity, max_uid = _seed_folder(source_id, user_id, None)  # INBOX
    row = ms.fetch_one(
        "INSERT INTO email_cleanup_job "
        "(user_id, source_id, kind, window_start, window_end, state, "
        " backfill_max_uid, backfill_uidvalidity) "
        "VALUES (%s, %s, 'window', %s, %s, 'backfilling', %s, %s) RETURNING id",
        (user_id, str(source_id), window_start, window_end, max_uid, uidvalidity),
    )
    return {"job_id": str(row["id"]), "state": "backfilling"}


def spam_folder_count(source_id: str, user_id: str) -> dict:
    adapter = eo._adapter_for(source_id, user_id)
    client = adapter.organize_session()
    try:
        junk = adapter._role_folder(client, "junk")
        trash = adapter._role_folder(client, "trash")
        count = 0
        if junk is not None:
            info = client.select_folder(junk, readonly=True)
            count = int(info.get(b"MESSAGES") or info.get("MESSAGES") or 0)
        return {"spam_count": count, "trash_available": trash is not None}
    finally:
        adapter.close_session(client)


def start_spam_job(source_id: str, user_id: str) -> dict:
    ms = config.get_metadata_store()
    if _active_job(ms, source_id) is not None:
        raise CleanupError("cleanup_active", 409)
    folder, uidvalidity, max_uid = _seed_folder(source_id, user_id, "junk")
    if folder is None:
        raise CleanupError("no_spam_folder", 409)
    row = ms.fetch_one(
        "INSERT INTO email_cleanup_job "
        "(user_id, source_id, kind, state, backfill_max_uid, backfill_uidvalidity) "
        "VALUES (%s, %s, 'spam', 'backfilling', %s, %s) RETURNING id",
        (user_id, str(source_id), max_uid, uidvalidity),
    )
    return {"job_id": str(row["id"]), "state": "backfilling"}


def approve(
    job_id: str,
    user_id: str,
    source_id: str,
    actions: list[dict],
    acknowledge_external_consumer: bool = False,
) -> dict:
    ms = config.get_metadata_store()
    job = _owned_job(ms, job_id, user_id, source_id)
    if job is None:
        raise CleanupError("not_found", 404)
    if job["state"] != "ready":
        raise CleanupError("job_not_ready", 409)
    # Keep only real, movable categories (drop 'other'/blank and move=false).
    clean = [
        {"category": a["category"], "move": True}
        for a in actions
        if a.get("category") and a["category"] != "other" and a.get("move")
    ]
    # LUM-729: the second arming point. This SCHEDULES the move rather than
    # performing it — `_run_window_execute` does the IMAP work on a later feed
    # tick — but this is the sole writer of 'executing' and therefore the moment
    # of consent, which is where a consent gate belongs. Gating the organize
    # route and not this one would fix the door and leave the window open.
    #
    # Only when something actually moves: an approval of nothing but non-moving
    # actions takes no mail out of INBOX and has nothing to warn about.
    if clean and not acknowledge_external_consumer:
        from services import external_consumer_guard as ecg

        if ecg.has_external_consumer(user_id):
            raise CleanupError(
                ecg.STARVATION_CODE, 422, detail=ecg.refusal_detail(ecg.ARM_BULK_MOVE)
            )
    ms.execute(
        "UPDATE email_cleanup_job SET approved_actions = %s::jsonb, state = 'executing', "
        "updated_at = NOW() WHERE id = %s",
        (json.dumps(clean), job_id),
    )
    return {"approved": len(clean)}


def undo_job(job_id: str, user_id: str, source_id: str, ops: list[str]) -> dict:
    ms = config.get_metadata_store()
    job = _owned_job(ms, job_id, user_id, source_id)
    if job is None:
        raise CleanupError("not_found", 404)
    valid = [o for o in ops if o in (eo._LABEL, eo._MOVE, eo._TRASH)] or [
        eo._LABEL,
        eo._MOVE,
        eo._TRASH,
    ]
    ms.execute(
        "UPDATE email_cleanup_job SET undo_ops = %s, undo_ticks = 0, state = 'undoing', "
        "updated_at = NOW() WHERE id = %s",
        (valid, job_id),
    )
    return {"undo_pending": True}


def job_summary(job_id: str, user_id: str, source_id: str) -> dict:
    ms = config.get_metadata_store()
    job = _owned_job(ms, job_id, user_id, source_id)
    if job is None:
        raise CleanupError("not_found", 404)
    max_uid = int(job["backfill_max_uid"] or 0)
    cursor = int(job["backfill_cursor"] or 0)
    progress = (
        1.0
        if job["state"] in ("ready", "done", "failed")
        else (min(cursor / max_uid, 1.0) if max_uid > 0 else 0.0)
    )
    base = {
        "state": job["state"],
        "kind": job["kind"],
        "progress": round(progress, 3),
    }
    if job["kind"] == "spam":
        base["spam_count"] = int(job["executed"] or 0)
        return base
    grouped = ms.fetch_all(
        "SELECT category, COUNT(*) AS n FROM email_cleanup_item "
        "WHERE job_id = %s AND category <> '' GROUP BY category ORDER BY category",
        (job_id,),
    )
    label_visible = _label_visible_for_job(ms, job)
    proposals = []
    for g in grouped:
        cat = g["category"]
        label = eo._label_for(user_id, cat)
        proposals.append(
            {
                "category": cat,
                "label": label,
                "count": int(g["n"]),
                "label_visible": label_visible,
                "can_move": bool(label) and cat != "other",
            }
        )
    base.update(
        {
            "window": {
                "start": str(job["window_start"]) if job["window_start"] else None,
                "end": str(job["window_end"]) if job["window_end"] else None,
            },
            "counts": {
                "scanned": int(job["scanned"] or 0),
                "classified": int(job["classified"] or 0),
                "labeled": int(job["labeled"] or 0),
                "executed": int(job["executed"] or 0),
            },
            "proposals": proposals,
        }
    )
    return base


def _label_visible_for_job(ms, job) -> bool:
    row = ms.fetch_one("SELECT url FROM sources WHERE id::text = %s", (str(job["source_id"]),))
    if not row:
        return False
    from urllib.parse import urlparse

    from services import email_providers

    host = urlparse(row.get("url") or "").hostname or ""
    return email_providers.label_visible_for_host(host)


# ---- the feed-tick driver -------------------------------------------------


def run_cleanup_tick(source) -> dict:
    """Advance the source's active cleanup job by one bounded step. Early-returns when
    no job is active (one indexed read), so idle mailboxes pay ~nothing. Best-effort —
    never raises to the caller (wrapped by feed_monitor)."""
    ms = config.get_metadata_store()
    job = _active_job(ms, str(source.id))
    if job is None:
        return {}
    state = job["state"]
    if state == "undoing":
        return _run_undo(ms, source, job)
    if job["kind"] == "spam":
        return _run_spam_walk(ms, source, job) if state == "backfilling" else {}
    # window
    if state == "backfilling":
        return _run_window_backfill(ms, source, job)
    if state == "executing":
        return _run_window_execute(ms, source, job)
    return {}


def _run_window_backfill(ms, source, job) -> dict:
    since = job["window_start"] or _EPOCH
    before = job["window_end"]
    after = int(job["backfill_cursor"] or 0)
    max_uid = int(job["backfill_max_uid"] or 0)
    if max_uid <= 0:
        _set_state(ms, job["id"], "ready")
        return {"state": "ready"}
    adapter = eo._adapter_for(str(source.id), source.user_id)
    try:
        uidvalidity, signals, _advance, empty = adapter.backfill_slice(
            since=since, before=before, after_uid=after, max_uid=max_uid
        )
    except Exception as exc:  # noqa: BLE001 - transient; retried next tick
        _log.warning(
            "email_cleanup_backfill_error source_id=%s type=%s", source.id, type(exc).__name__
        )
        return {}
    if job["backfill_uidvalidity"] and uidvalidity != int(job["backfill_uidvalidity"]):
        try:
            nv, nmax = adapter.inbox_seed()
            ms.execute(
                "UPDATE email_cleanup_job SET backfill_cursor = 0, backfill_max_uid = %s, "
                "backfill_uidvalidity = %s, updated_at = NOW() WHERE id = %s",
                (nmax, nv, job["id"]),
            )
        except Exception:  # noqa: BLE001
            _log.warning("email_cleanup_reseed_error source_id=%s", source.id)
        return {}
    if empty:
        _set_state(ms, job["id"], "ready")
        return {"state": "ready"}

    from services.signal_processor import process_signal

    urls = [raw.url for raw in signals]
    safe = after
    for raw in signals:
        uid = _uid_from_url(raw.url)
        if uid is None:
            continue
        if not _is_duplicate(ms, raw.url, source.user_id):
            try:
                process_signal(raw, user_id=source.user_id, from_backfill=True)
            except Exception as exc:  # noqa: BLE001 - poison message; advance past it
                _log.error(
                    "email_cleanup_persist_error source_id=%s uid=%s type=%s",
                    source.id,
                    uid,
                    type(exc).__name__,
                )
                break
        safe = max(safe, uid)

    rows = (
        ms.fetch_all(
            "SELECT url, category FROM signals WHERE source_id = %s AND url = ANY(%s)",
            (str(source.id), urls),
        )
        or []
    )
    for r in rows:
        ms.execute(
            "INSERT INTO email_cleanup_item (job_id, signal_url, category) VALUES (%s, %s, %s) "
            "ON CONFLICT (job_id, signal_url) DO UPDATE SET category = EXCLUDED.category",
            (job["id"], r["url"], r["category"] or ""),
        )
    labeled = _auto_label(ms, source, job, rows)
    ms.execute(
        "UPDATE email_cleanup_job SET backfill_cursor = %s, backfill_uidvalidity = %s, "
        "scanned = scanned + %s, classified = classified + %s, labeled = labeled + %s, "
        "updated_at = NOW() WHERE id = %s",
        (safe, uidvalidity, len(signals), len(rows), labeled, job["id"]),
    )
    return {"scanned": len(signals), "labeled": labeled}


def _auto_label(ms, source, job, rows) -> int:
    """Auto-apply labels for label-visible providers, unless the standing organize already
    labels (organize_label_enabled). Keywords are invisible on iCloud/Fastmail, so skip
    non-label-visible providers (LUM-645 steer). Returns the count of rows written."""
    if not rows or not _label_visible(source) or _standing_label_on(ms, str(source.id)):
        return 0
    adapter = eo._adapter_for(str(source.id), source.user_id)
    client = adapter.organize_session()
    labeled = 0
    try:
        for r in rows:
            cat = r["category"] or ""
            if not cat or cat == "other":
                continue
            label = eo._label_for(source.user_id, cat)
            if not label:
                continue
            if eo._safe_apply(
                adapter,
                source_id=str(source.id),
                user_id=source.user_id,
                signal_url=r["url"],
                op=eo._LABEL,
                label=eo._sanitize_artifact_name(label),
                slug=cat,
                cleanup_job_id=str(job["id"]),
                client=client,
            ):
                labeled += 1
    finally:
        adapter.close_session(client)
    return labeled


def _run_window_execute(ms, source, job) -> dict:
    actions = job["approved_actions"]
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except ValueError:
            actions = []
    move_cats = [a["category"] for a in (actions or []) if a.get("move") and a.get("category")]
    if not move_cats:
        _set_state(ms, job["id"], "done")
        return {"state": "done"}
    rows = (
        ms.fetch_all(
            "SELECT i.signal_url AS url, i.category AS category FROM email_cleanup_item i "
            "LEFT JOIN email_organize_log l "
            "  ON l.signal_url = i.signal_url AND l.op = 'move' AND l.reversed_at IS NULL "
            "WHERE i.job_id = %s AND i.category = ANY(%s) AND l.id IS NULL LIMIT %s",
            (job["id"], move_cats, _TICK_LIMIT),
        )
        or []
    )
    if not rows:
        _set_state(ms, job["id"], "done")
        return {"state": "done"}
    adapter = eo._adapter_for(str(source.id), source.user_id)
    client = adapter.organize_session()
    executed = 0
    try:
        for r in rows:
            label = eo._label_for(source.user_id, r["category"])
            if not label:
                continue
            if eo._safe_apply(
                adapter,
                source_id=str(source.id),
                user_id=source.user_id,
                signal_url=r["url"],
                op=eo._MOVE,
                label=eo._sanitize_artifact_name(label),
                slug=r["category"],
                cleanup_job_id=str(job["id"]),
                client=client,
            ):
                executed += 1
    finally:
        adapter.close_session(client)
    ms.execute(
        "UPDATE email_cleanup_job SET executed = executed + %s, updated_at = NOW() WHERE id = %s",
        (executed, job["id"]),
    )
    if len(rows) < _TICK_LIMIT:
        _set_state(ms, job["id"], "done")
    return {"executed": executed}


def _run_spam_walk(ms, source, job) -> dict:
    after = int(job["backfill_cursor"] or 0)
    max_uid = int(job["backfill_max_uid"] or 0)
    if max_uid <= 0:
        _set_state(ms, job["id"], "done")
        return {"state": "done"}
    adapter = eo._adapter_for(str(source.id), source.user_id)
    client = adapter.organize_session()
    executed = 0
    try:
        folder, uidvalidity, uids, advance, empty = adapter.enumerate_role_folder(
            client, role="junk", after_uid=after, max_uid=max_uid
        )
        if folder is None:
            _set_state(ms, job["id"], "done")
            return {"state": "done"}
        if job["backfill_uidvalidity"] and uidvalidity != int(job["backfill_uidvalidity"]):
            uv, un = adapter.folder_uidnext(client, folder)
            ms.execute(
                "UPDATE email_cleanup_job SET backfill_cursor = 0, backfill_max_uid = %s, "
                "backfill_uidvalidity = %s, updated_at = NOW() WHERE id = %s",
                (max(un - 1, 0), uv, job["id"]),
            )
            return {}
        if empty:
            _set_state(ms, job["id"], "done")
            return {"state": "done"}
        safe = after
        for uid in uids:
            surl = _synthetic_url(str(source.id), uidvalidity, uid)
            if eo._safe_apply(
                adapter,
                source_id=str(source.id),
                user_id=source.user_id,
                signal_url=surl,
                op=eo._TRASH,
                label="",
                slug="",
                mailbox=folder,
                cleanup_job_id=str(job["id"]),
                client=client,
            ):
                executed += 1
                safe = max(safe, uid)
                ms.execute(
                    "INSERT INTO email_cleanup_item (job_id, signal_url, category) "
                    "VALUES (%s, %s, '__spam__') ON CONFLICT (job_id, signal_url) DO NOTHING",
                    (job["id"], surl),
                )
            else:
                # Mirror window backfill: do not advance the cursor past a failed trash
                # op — the UID stays in the junk folder and must be retried next tick.
                break
    finally:
        adapter.close_session(client)
    ms.execute(
        "UPDATE email_cleanup_job SET backfill_cursor = %s, backfill_uidvalidity = %s, "
        "scanned = scanned + %s, executed = executed + %s, updated_at = NOW() WHERE id = %s",
        (safe, uidvalidity, len(uids), executed, job["id"]),
    )
    return {"trashed": executed}


def _run_undo(ms, source, job) -> dict:
    ops = list(job["undo_ops"] or [eo._LABEL, eo._MOVE, eo._TRASH])
    rows = (
        ms.fetch_all(
            "SELECT id, signal_url, op, reverse_token FROM email_organize_log "
            "WHERE cleanup_job_id = %s AND reversed_at IS NULL AND op = ANY(%s) "
            "ORDER BY (op = 'move' OR op = 'trash') DESC, id ASC LIMIT %s",
            (str(job["id"]), ops, _TICK_LIMIT),
        )
        or []
    )
    if not rows:
        _set_state(ms, job["id"], "done")
        ms.execute("UPDATE email_cleanup_job SET undo_ops = NULL WHERE id = %s", (job["id"],))
        return {"state": "done"}
    adapter = eo._adapter_for(str(source.id), source.user_id)
    client = adapter.organize_session()
    reversed_n = 0
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
                reversed_n += 1
            except Exception as exc:  # noqa: BLE001 - counted, row stays active, retried
                _log.warning(
                    "email_cleanup_reverse_failed url=%s type=%s",
                    r["signal_url"],
                    type(exc).__name__,
                )
    finally:
        adapter.close_session(client)
    remaining = ms.fetch_one(
        "SELECT 1 FROM email_organize_log WHERE cleanup_job_id = %s AND reversed_at IS NULL "
        "AND op = ANY(%s) LIMIT 1",
        (str(job["id"]), ops),
    )
    ticks = int(job["undo_ticks"] or 0) + 1
    if remaining is None:
        ms.execute(
            "UPDATE email_cleanup_job SET reversed = reversed + %s, undo_ops = NULL, "
            "state = 'done', updated_at = NOW() WHERE id = %s",
            (reversed_n, job["id"]),
        )
    elif ticks >= _UNDO_MAX_TICKS:
        # Give up on irreversible rows so the job stops blocking new jobs.
        ms.execute(
            "UPDATE email_cleanup_job SET reversed = reversed + %s, undo_ops = NULL, "
            "state = 'failed', undo_ticks = %s, updated_at = NOW() WHERE id = %s",
            (reversed_n, ticks, job["id"]),
        )
    else:
        ms.execute(
            "UPDATE email_cleanup_job SET reversed = reversed + %s, undo_ticks = %s, "
            "updated_at = NOW() WHERE id = %s",
            (reversed_n, ticks, job["id"]),
        )
    return {"reversed": reversed_n}
