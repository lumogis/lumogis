# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Feed monitor: polls RSS/Atom/JSON feeds and static pages on a schedule.

On start():
  - Loads all active sources from Postgres.
  - Schedules one APScheduler IntervalTrigger job per source, named
    "signal_poll_{source_id}", at source.poll_interval seconds.

schedule_source(source) is also called from routes/signals.py when a new
source is added via POST /sources?confirm=true, so it can start polling
immediately without a restart.
"""

import logging
import threading
from dataclasses import replace
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Literal
from typing import Optional

from models.signals import SourceConfig
from services.signal_processor import process_signal

import config

_log = logging.getLogger(__name__)

# Every column a ``SourceConfig`` is built from. ``start()``, ``reconcile_source_schedule``
# and ``enqueue_sync_now`` all select through this one list so none of them can
# silently take a ``_row_to_source`` default (a dropped ``user_id`` would become
# ``"default"``). The ``id, name, source_type, url, category`` prefix is
# deliberate: the route-test fakes match on it.
_SOURCE_POLL_COLUMNS = (
    "id, name, source_type, url, category, active, poll_interval, "
    "extraction_method, css_selector_override, last_polled_at, last_signal_at, "
    "poll_cursor, user_id"
)


def start() -> None:
    """Load active sources from Postgres and schedule poll jobs.

    The ``WHERE active = TRUE`` filter is the whole of "a paused source stays
    paused across a restart" (LUM-844). It is pinned structurally by
    ``test_startup_schedules_no_job_for_a_paused_source``; no automated test
    restarts a real stack, and the plan's manual curl block (pause → restart →
    still not polling) was not run at verify. Run it before relying on restart
    persistence in a release note.
    """
    scheduler = config.get_scheduler()
    if not scheduler.running:
        _log.info("feed_monitor: scheduler not running yet, skipping source load")
        return

    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(f"SELECT {_SOURCE_POLL_COLUMNS} FROM sources WHERE active = TRUE")
    except Exception as exc:
        _log.warning("feed_monitor: could not load sources from Postgres: %s", exc)
        return

    for row in rows:
        source = _row_to_source(row)
        schedule_source(source)

    _log.info("feed_monitor: scheduled %d source poll jobs", len(rows))


def stop() -> None:
    """Remove all signal_poll_* jobs from the scheduler."""
    try:
        scheduler = config.get_scheduler()
        for job in scheduler.get_jobs():
            if job.id.startswith("signal_poll_"):
                job.remove()
        _log.info("feed_monitor: removed all poll jobs")
    except Exception as exc:
        _log.warning("feed_monitor stop error: %s", exc)


def unschedule_source(source_id: str) -> None:
    """Remove the poll job for a single source, if scheduled (LUM-701).

    No-op if the job doesn't exist (already removed, or was never scheduled —
    e.g. a paused/inactive source). Used by ``reconcile_source_schedule`` (pause
    and delete). Check-then-act: call it only under ``_source_schedule_lock``.
    """
    scheduler = config.get_scheduler()
    job_id = f"signal_poll_{source_id}"
    if scheduler.get_job(job_id) is not None:
        scheduler.remove_job(job_id)


def schedule_source(source: SourceConfig) -> None:
    """Add or replace the poll job for a single source."""
    scheduler = config.get_scheduler()
    job_id = f"signal_poll_{source.id}"

    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()

    scheduler.add_job(
        _poll_source,
        trigger="interval",
        seconds=max(60, source.poll_interval),
        args=[source],
        id=job_id,
        name=f"Poll {source.name} ({source.source_type})",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _log.info("Scheduled poll job %s every %ds", job_id, source.poll_interval)


def _add_sync_now_job(scheduler, source: SourceConfig) -> None:
    """Add the one-off ``signal_sync_now_{id}`` job (shared by Sync now and resume)."""
    scheduler.add_job(
        _poll_source,
        trigger="date",
        run_date=datetime.now(),
        args=[source],
        id=f"signal_sync_now_{source.id}",
        name=f"Sync now {source.name}",
        max_instances=1,
        coalesce=True,
        replace_existing=False,
        misfire_grace_time=None,
    )


# Serialises every scheduler mutation that follows a ``sources`` write, plus the
# Sync-now enqueue (LUM-844). In-process only, which is valid because LUM-832
# refuses multi-worker boot. Only ``reconcile_source_schedule`` and
# ``enqueue_sync_now`` take it; neither calls the other, and nothing called under
# it may take it again — it is a plain ``Lock``, not an ``RLock``, so a nested
# acquire deadlocks loudly in tests instead of hiding a re-entrant design.
# ``start()`` does not take it: it runs in lifespan before any request.
_source_schedule_lock = threading.Lock()

# Sources whose DELETE is in progress (guarded by ``_source_schedule_lock``). While
# a source is in here it reads as paused, whatever its column says: delete pauses
# the row before its slow vector purge, but a concurrent ``PATCH {"active": true}``
# would otherwise re-add a poll job (and an immediate check) that ingests into
# vectors the purge just removed.
_deleting: set[str] = set()


def begin_delete(source_id: str) -> None:
    """Mark a source as being deleted: reconcile and Sync now treat it as paused."""
    with _source_schedule_lock:
        _deleting.add(source_id)


def end_delete(source_id: str) -> None:
    """Clear the mark set by ``begin_delete`` (always call it, in a ``finally``)."""
    with _source_schedule_lock:
        _deleting.discard(source_id)


def reconcile_source_schedule(
    source_id: str,
    user_id: str,
    *,
    run_now: bool = False,
    stop_on_error: bool = False,
) -> Optional[bool]:
    """Make the scheduler match the ``sources.active`` column for one source.

    The column is the only durable record of paused; this is called after the
    write has committed. The last caller wins, and the last caller reads after
    every committed write — which is what closes the edit-versus-pause race that
    deciding from the UPDATE's own ``RETURNING`` leaves open.

    Returns the ``active`` value it read (``None`` = row gone). The poll job is
    scheduled exactly when it returns ``True``. A poll that is already running is
    not interrupted. Exceptions propagate after the ``stop_on_error`` clean-up.

    ``stop_on_error`` is a fail-closed trade-off: when the re-read itself failed
    the real state is unknown, so both jobs are removed (under the lock, so it is
    ordered against other reconciles). It does **not** remove them when the row
    was read as active — a failure after that point leaves the old job rather
    than killing a source a concurrent resume just started.
    """
    read_active: Optional[bool] = None
    try:
        with _source_schedule_lock:
            ms = config.get_metadata_store()
            row = ms.fetch_one(
                f"SELECT {_SOURCE_POLL_COLUMNS} FROM sources WHERE id::text = %s AND user_id = %s",
                (source_id, user_id),
            )
            read_active = bool(row is not None and row["active"])
            scheduler = config.get_scheduler()
            if read_active and source_id not in _deleting:
                source = _row_to_source(row)
                schedule_source(source)
                if run_now and scheduler.get_job(f"signal_sync_now_{source_id}") is None:
                    _add_sync_now_job(scheduler, source)
                return True
            unschedule_source(source_id)
            try:
                scheduler.remove_job(f"signal_sync_now_{source_id}")
            except KeyError:
                # APScheduler removes a run-once job from its own thread once it
                # fires, outside _source_schedule_lock; already gone is the
                # outcome we wanted.
                pass
            return False if row is not None else None
    except Exception:
        if stop_on_error and not read_active:
            with _source_schedule_lock:
                _remove_jobs_best_effort(source_id)
        raise


def _remove_jobs_best_effort(source_id: str) -> None:
    """Remove both jobs, ignoring every error. Call under ``_source_schedule_lock``."""
    for job_id in (f"signal_poll_{source_id}", f"signal_sync_now_{source_id}"):
        try:
            config.get_scheduler().remove_job(job_id)
        except Exception:
            pass


def enqueue_sync_now(
    source_id: str, user_id: str
) -> Literal["queued", "not_found", "paused", "already_running"]:
    """Queue one immediate poll of an owned source ("Sync now").

    Runs under ``_source_schedule_lock`` so it cannot interleave with a pause.
    A paused source is refused (checked before the already-queued check).
    """
    with _source_schedule_lock:
        ms = config.get_metadata_store()
        row = ms.fetch_one(
            f"SELECT {_SOURCE_POLL_COLUMNS} FROM sources WHERE id::text = %s AND user_id = %s",
            (source_id, user_id),
        )
        if row is None:
            return "not_found"
        if not row["active"] or source_id in _deleting:
            return "paused"
        scheduler = config.get_scheduler()
        if scheduler.get_job(f"signal_sync_now_{source_id}") is not None:
            return "already_running"
        _add_sync_now_job(scheduler, _row_to_source(row))
        return "queued"


def _poll_source(source: SourceConfig) -> None:
    """Dispatcher: paperless REST ingest vs email vs RSS-style signal adapters."""
    if source.source_type == "paperless":
        _poll_paperless_source(source)
    elif source.source_type == "imap":
        _poll_email_dispatch(source)
    else:
        _poll_signal_source(source)


def _email_full_ingest_enabled() -> bool:
    import os

    return os.environ.get("EMAIL_FULL_INGEST_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


_email_poll_inflight: set[str] = set()
_email_poll_inflight_lock = threading.Lock()


def _poll_email_dispatch(source: SourceConfig) -> None:
    """Route imap polls to metadata or full-ingest, guarded against overlap.

    A scheduled poll and a manual "Sync now" run as different APScheduler jobs
    in the same scheduler process, so ``max_instances=1`` (per job id) does not
    stop them overlapping. A lightweight in-process in-flight set keyed by
    ``source.id`` makes them mutually exclusive **within the process**, which is
    the single-scheduler family-LAN deployment.

    Cross-process (multi-worker) overlap is intentionally NOT locked here — it is
    harmless because dedup-by-url (``_is_duplicate``) and the
    ``external_documents`` UNIQUE key drop any duplicate signal/document. We do
    NOT hold a Postgres advisory lock across the poll: ``consolidation_lock``
    explicitly forbids wrapping slow (IMAP/LLM) work in its handle, and holding a
    dedicated connection for the poll duration is the wrong trade-off.
    """
    with _email_poll_inflight_lock:
        if source.id in _email_poll_inflight:
            _log.info("email_poll_coalesced source_id=%s (in-process poll in flight)", source.id)
            return
        _email_poll_inflight.add(source.id)
    try:
        if source.extraction_method == "imap_full_ingest" and _email_full_ingest_enabled():
            _poll_email_ingest_source(source)
        else:
            _poll_email_source(source)
    finally:
        with _email_poll_inflight_lock:
            _email_poll_inflight.discard(source.id)


def _poll_email_source(source: SourceConfig) -> None:
    """IMAP metadata poll (LUM-170) — cursor-managing, mirrors paperless.

    Unlike the generic ``_poll_signal_source`` (whose adapters are stateless
    fetchers), the email cursor lives HERE, not in the adapter: re-read from
    Postgres at poll start, advance + persist after processing. APScheduler
    re-passes a frozen ``SourceConfig`` each tick, so an in-adapter cursor
    would be lost between ticks and on restart.
    """
    _log.info("Polling email source: %s (%s)", source.name, source.id)
    from adapters.email_imap_source import EmailImapSource

    cursor = _fetch_poll_cursor(source.id)
    imp = _fetch_import_row(source.id)
    try:
        adapter = EmailImapSource(source)
    except Exception as exc:
        _log.warning(
            "email_poll_skip source_id=%s code=%s type=%s msg=%s",
            source.id,
            _classify_construction_failure(exc),
            type(exc).__name__,
            str(exc)[:200],
        )
        _record_poll_health(source.id, ok=False, error_code=_classify_construction_failure(exc))
        _update_poll_timestamp(source)
        return
    # NO health stamp here (LUM-633). Construction opens no IMAP socket, so
    # "constructed" is not "connected" — stamping healthy here reported an
    # egress-blocked mailbox as fine. Health is now recorded at each connecting
    # call below, via _record_connect_outcome.

    import_state = imp.get("import_state")

    # (1) Seed once — set the live cursor to *now* (UIDNEXT-1) for every scope, then
    # resolve just_new straight to 'done' or enqueue a bounded backfill (LUM-644).
    # Guard on state=='pending' (not on cursor) so a crash between the poll_cursor
    # write and the backfill enqueue still recovers on the next tick; the live
    # cursor itself is seeded only when NULL (a re-run never re-seeds it).
    if import_state == "pending":
        try:
            uidvalidity, max_uid = adapter.inbox_seed()
            _record_connect_outcome(source.id, None)
        except Exception as exc:
            _record_connect_outcome(source.id, exc)
            _log.error(
                "email_seed_error source_id=%s type=%s msg=%s",
                source.id,
                type(exc).__name__,
                str(exc)[:200],
            )
            _update_poll_timestamp(source)
            return
        if cursor is None:  # seed-once guard: live cursor starts at *now*
            cursor = f"{uidvalidity}:{max_uid}"
            _persist_poll_cursor(source.id, cursor)
        if imp.get("import_scope") == "just_new":
            _set_import_state(source.id, "done")
            import_state = "done"
        else:
            # Resolve the SINCE lower bound: last_7d/last_30d store NULL in the row,
            # so compute the window here (custom carries an explicit import_since).
            since = _resolve_import_since(imp.get("import_scope"), imp.get("import_since"))
            _start_backfill(source.id, since=since, max_uid=max_uid, uidvalidity=uidvalidity)
            import_state = "backfilling"
            imp.update(
                import_state="backfilling",
                import_since=since,
                backfill_cursor=0,
                backfill_max_uid=max_uid,
                backfill_uidvalidity=uidvalidity,
            )

    # (2) Live poll ALWAYS runs (never starved by backfill). Scope-bearing sources
    # re-seed on a UIDVALIDITY change instead of re-draining full history.
    scope_bearing = import_state is not None
    try:
        raw_signals = adapter.poll(since=cursor, reseed_on_uidvalidity_change=scope_bearing)
        _record_connect_outcome(source.id, None)
        _, last_uid = _parse_email_poll_cursor(cursor)
        safe_uid = last_uid
        new_count = 0
        for raw in raw_signals:
            uid = _email_uid_from_url(raw.url)
            if uid is None:
                continue
            if _is_duplicate(raw.url, source.user_id):
                safe_uid = max(safe_uid, uid)
                continue
            try:
                process_signal(raw, user_id=source.user_id)
                new_count += 1
                safe_uid = max(safe_uid, uid)
            except Exception as exc:
                _log.error(
                    "signal_processor error for email uid %r: type=%s msg=%s",
                    raw.url,
                    type(exc).__name__,
                    str(exc)[:200],
                )
                # Do not advance the cursor past a row that failed to persist.
                break
        safe_cursor = _email_poll_cursor_for_uid(adapter.result_cursor, safe_uid)
        if safe_cursor:
            _persist_poll_cursor(source.id, safe_cursor)
        _log.info(
            "Polled email %s: %d new signals (of %d fetched)",
            source.name,
            new_count,
            len(raw_signals),
        )
    except Exception as exc:
        _record_connect_outcome(source.id, exc)
        _log.error(
            "email_poll_error source_id=%s type=%s msg=%s",
            source.id,
            type(exc).__name__,
            str(exc)[:200],
        )

    # (3) Backfill slice — additive historical import, its own cursor, never touches
    # poll_cursor. Runs after live poll so a failing backfill can't starve live mail.
    if import_state == "backfilling":
        _run_backfill_slice(source, adapter, imp)

    # (4) LUM-645 organize sweep — one bounded tick for an opt-in source: undo if a
    # reversal is pending, else the standing reconcile sweep (a genuine retry net for
    # transiently-failed go-forward writes). Best-effort; never breaks the poll.
    _run_organize_tick(source)

    # (5) LUM-646 inbox-cleanup sweep — advance the source's active cleanup job by one
    # bounded step. Idle sources early-return after one indexed read. Best-effort.
    _run_cleanup_tick(source)

    _update_poll_timestamp(source)


def _email_uid_from_url(url: str) -> int | None:
    """Extract the UID from an ``imap://{source_id}/{uidvalidity}/{uid}`` url."""
    try:
        return int(url.rsplit("/", 1)[1])
    except (ValueError, IndexError):
        return None


def _parse_email_poll_cursor(cursor: str | None) -> tuple[int | None, int]:
    """Return ``(uidvalidity, last_uid)`` from a ``"{uidvalidity}:{last_uid}"`` cursor."""
    if not cursor:
        return (None, 0)
    try:
        uv_s, uid_s = cursor.split(":", 1)
        return (int(uv_s), int(uid_s))
    except (ValueError, AttributeError):
        return (None, 0)


def _email_poll_cursor_for_uid(result_cursor: str | None, last_uid: int) -> str | None:
    """Build the poll cursor to persist after processing up to ``last_uid``."""
    if not result_cursor:
        return None
    try:
        uidvalidity_s, _ = result_cursor.split(":", 1)
        return f"{int(uidvalidity_s)}:{last_uid}"
    except (ValueError, AttributeError):
        return None


def _poll_email_ingest_source(source: SourceConfig) -> None:
    """IMAP full-ingest (LUM-170, opt-in via EMAIL_FULL_INGEST_ENABLED).

    Metadata + classification exactly as ``_poll_email_source`` (writes a signal
    row per message with its category), PLUS the message body is ingested into
    the document store via ``ingest_external_document`` (``external_kind="email"``,
    dedup by ``external_document_id``). The IMAP UID cursor is NOT threaded
    through that function's lexical watermark comparison (which is ISO-timestamp
    shaped) — we pass ``stored_source_poll_cursor=None`` to disable its skew
    skip, and this function's own ``_persist_poll_cursor`` write is authoritative.
    """
    _log.info("Polling email (full-ingest) source: %s (%s)", source.name, source.id)
    from adapters.email_imap_source import EmailImapSource
    from services.ingest import ingest_external_document

    cursor = _fetch_poll_cursor(source.id)
    try:
        adapter = EmailImapSource(source)
    except Exception as exc:
        _log.warning(
            "email_full_ingest_skip source_id=%s code=%s type=%s msg=%s",
            source.id,
            _classify_construction_failure(exc),
            type(exc).__name__,
            str(exc)[:200],
        )
        _record_poll_health(source.id, ok=False, error_code=_classify_construction_failure(exc))
        _update_poll_timestamp(source)
        return
    # NO health stamp here — see the note in _poll_email_source (LUM-633).
    try:
        raw_signals = adapter.poll(since=cursor)
        _record_connect_outcome(source.id, None)
    except Exception as exc:
        _record_connect_outcome(source.id, exc)
        _log.warning(
            "email_full_ingest_skip source_id=%s type=%s msg=%s",
            source.id,
            type(exc).__name__,
            str(exc)[:200],
        )
        _update_poll_timestamp(source)
        return

    # Map uid -> signal for the messages we're about to process.
    by_uid: dict[int, object] = {}
    for raw in raw_signals:
        uid = _email_uid_from_url(raw.url)
        if uid is not None:
            by_uid[uid] = raw

    # LUM-695: one fetch, one parse — text AND attachment metadata. The metadata
    # is extracted inside the adapter, where the raw MIME bytes are; by the time
    # a Signal reaches process_signal, raw_content is already a string.
    # An adapter without the sibling (a test double predating LUM-695) degrades
    # to bodies-only rather than losing the poll tick entirely.
    bodies: dict[int, str] = {}
    attachments_by_uid: dict[int, list] = {}
    dsn_by_uid: dict[int, object] = {}
    try:
        fetch_both = getattr(adapter, "fetch_bodies_and_attachments", None)
        if callable(fetch_both):
            for uid, both in (fetch_both(list(by_uid.keys())) or {}).items():
                bodies[uid] = both.text
                attachments_by_uid[uid] = list(both.attachments)
                if getattr(both, "dsn", None) is not None:
                    dsn_by_uid[uid] = both.dsn
        else:
            bodies = adapter.fetch_bodies(list(by_uid.keys()))
        _record_connect_outcome(source.id, None)
    except Exception as exc:
        _record_connect_outcome(source.id, exc)
        _log.error(
            "email_body_fetch_error source_id=%s type=%s msg=%s",
            source.id,
            type(exc).__name__,
            str(exc)[:200],
        )

    _, last_uid = _parse_email_poll_cursor(cursor)
    safe_uid = last_uid
    new_count = 0
    for uid, raw in sorted(by_uid.items()):
        if _is_duplicate(raw.url, source.user_id):
            safe_uid = max(safe_uid, uid)
            continue
        # LUM-820 — a delivery-status notification is not correspondence. It is
        # recorded against the send it reports on and does NOT become a signal,
        # so it cannot sit unread in a triage lane while the audit row still
        # claims the message was delivered.
        #
        # An UNCORRELATED report deliberately falls through to the ordinary path
        # below: losing a bounce entirely is the one outcome worse than not
        # tying it to a send, and the user can at least still see it.
        dsn = dsn_by_uid.get(uid)
        if dsn is not None:
            from services import email_dsn as _dsn_svc

            try:
                if _dsn_svc.record_bounce(dsn, source.user_id):
                    safe_uid = max(safe_uid, uid)
                    continue
            except Exception as exc:  # noqa: BLE001 - never stall a poll on this
                _log.warning(
                    "email_dsn_record_failed source_id=%s uid=%s type=%s",
                    source.id,
                    uid,
                    type(exc).__name__,
                )

        # Attach the body so classification/relevance can use it locally.
        body = bodies.get(uid, "")
        raw.raw_content = body
        # Transient, like `headers`: process_signal writes these to
        # `email_attachment` after the LUM-697 suppression return (LUM-695).
        raw.attachments = attachments_by_uid.get(uid, [])
        if not body:
            # Full-ingest source but no body this tick (fetch capped at
            # _MAX_EMAIL_BODIES_PER_TICK, fetch error, or HTML-only mail):
            # body-keyword rules (LUM-630) cannot fire on this signal and it is
            # deduped by URL, so the miss is permanent. Diagnosable, not fatal.
            _log.debug("email_body_absent_on_full_ingest source_id=%s uid=%s", source.id, uid)
        try:
            process_signal(raw, user_id=source.user_id)
            new_count += 1
        except Exception as exc:
            _log.error("signal_processor error for email uid %s: %s", uid, str(exc)[:200])
            break

        if body:
            # UIDVALIDITY is the middle url segment: imap://{source_id}/{uidvalidity}/{uid}
            parts = raw.url.split("/")
            uidvalidity = parts[-2] if len(parts) >= 2 else "0"
            try:
                ingest_external_document(
                    user_id=source.user_id,
                    source_id=str(source.id),
                    external_kind="email",
                    external_document_id=f"{uidvalidity}:{uid}",
                    content=body,
                    # NOT the IMAP cursor — external_document_id dedups; the skew
                    # guard is disabled by stored=None; our _persist_poll_cursor wins.
                    poll_watermark=f"{uidvalidity}:{uid}",
                    stored_source_poll_cursor=None,
                )
            except Exception as exc:
                _log.error(
                    "email_ingest_failed source_id=%s uid=%s msg=%s",
                    source.id,
                    uid,
                    str(exc)[:200],
                )
                break

        safe_uid = max(safe_uid, uid)

    safe_cursor = _email_poll_cursor_for_uid(adapter.result_cursor, safe_uid)
    if safe_cursor:
        _persist_poll_cursor(source.id, safe_cursor)
    _update_poll_timestamp(source)
    _log.info(
        "Polled email full-ingest %s: %d new signals (of %d fetched)",
        source.name,
        new_count,
        len(raw_signals),
    )


def _poll_signal_source(source: SourceConfig) -> None:
    """RSS/page/playwright/caldav path: adapter poll → dedup → process_signal."""
    _log.info("Polling source: %s (%s)", source.name, source.source_type)

    adapter = _build_adapter(source)
    if adapter is None:
        return

    try:
        raw_signals = adapter.poll()
    except Exception as exc:
        _log.error(
            "Poll error for source %s: type=%s msg=%s",
            source.id,
            type(exc).__name__,
            str(exc)[:200],
        )
        # LUM-837: health is recorded for RSS/page sources too, not just IMAP.
        # Before this, a feed whose host had gone away failed silently forever —
        # `_update_poll_timestamp` below still advanced `last_polled_at`, so any
        # consumer of these columns read a dead source as freshly polled.
        _record_poll_health(source.id, ok=False, error_code="poll_failed")
        raw_signals = []
    else:
        # **Ask the adapter, do not infer from the absence of an exception.**
        # Neither adapter raises on a fetch failure: `PageScraper._fetch_html`
        # catches and returns None, and `feedparser.parse` does not raise on a
        # network error at all — both simply return an empty list, which is
        # indistinguishable from "reached it, nothing new". So the `except`
        # branch above essentially never fires in production, and LUM-837's
        # first version of this recorded `ok=True` for a source that had not
        # been reachable in weeks. `last_fetch_ok` is the adapters' answer to
        # the question the exception could not answer.
        if getattr(adapter, "last_fetch_ok", True):
            _record_poll_health(source.id, ok=True)
        else:
            _record_poll_health(source.id, ok=False, error_code="poll_failed")

    new_count = 0
    for raw in raw_signals:
        if _is_duplicate(raw.url, source.user_id):
            continue
        try:
            process_signal(raw, user_id=source.user_id)
            new_count += 1
        except Exception as exc:
            _log.error(
                "signal_processor error for %r: type=%s msg=%s",
                raw.title[:60],
                type(exc).__name__,
                str(exc)[:200],
            )

    _update_poll_timestamp(source)
    _log.info("Polled %s: %d new signals (of %d fetched)", source.name, new_count, len(raw_signals))


_MAX_PAPERLESS_DOCS_PER_TICK = 500
_MAX_PAPERLESS_CHUNKS_PER_TICK = 2000


def _fetch_poll_cursor(source_id: str) -> str | None:
    try:
        ms = config.get_metadata_store()
        row = ms.fetch_one("SELECT poll_cursor FROM sources WHERE id = %s", (source_id,))
    except Exception:
        return None
    if not row:
        return None
    v = row.get("poll_cursor")
    if v is None or v == "":
        return None
    return str(v)


def _poll_paperless_source(source: SourceConfig) -> None:
    """paperless-ngx incremental ingest — never calls ``process_signal``."""
    _log.info("Polling paperless source: %s (%s)", source.name, source.id)
    from adapters.paperless_source import PaperlessPoller
    from services.ingest import chunk_text
    from services.ingest import ingest_external_document

    from services import paperless_credentials as pwc

    cursor = _fetch_poll_cursor(source.id)
    source = replace(source, poll_cursor=cursor)
    # Paperless uses ``added__gt=<since_cursor>`` for incremental fetch. That
    # filter is **strict** on ``added``. If we advance ``since_cursor`` between
    # HTTP pages (after each ingested row), page N+1 can drop every document that
    # still shares the same ``added`` timestamp as an earlier page — a silent
    # skip when bulk imports stamp identical timestamps. Keep one watermark
    # for all pages in this poll tick; ``ingest_external_document`` still
    # advances ``sources.poll_cursor`` in Postgres per document.
    fetch_since = cursor

    try:
        conn = pwc.load_connection(source.user_id)
    except Exception as exc:
        _log.warning(
            "paperless_poll_skip connector=%s code=credential user_id=%s type=%s msg=%s",
            "paperless",
            source.user_id,
            type(exc).__name__,
            str(exc)[:200],
        )
        # LUM-837: a paperless source with no credential can never work, and
        # before this it advanced `last_polled_at` and recorded nothing — so a
        # source that had never once succeeded read as freshly polled to every
        # consumer of these columns. `POST /sources` does not require the
        # credential to exist first, so this is a reachable steady state, not a
        # transient.
        _record_poll_health(source.id, ok=False, error_code="credential")
        _update_poll_timestamp(source)
        return

    cred_base = conn.base_url.rstrip("/")
    src_url = (source.url or "").rstrip("/")
    if cred_base != src_url:
        _log.warning(
            "paperless_url_mismatch source_id=%s credential_base=%r sources_url=%r",
            source.id,
            cred_base,
            src_url,
        )

    poller = PaperlessPoller(conn)
    docs_done = 0
    chunks_used = 0
    page = 1
    auth_failed = False

    while docs_done < _MAX_PAPERLESS_DOCS_PER_TICK and chunks_used < _MAX_PAPERLESS_CHUNKS_PER_TICK:
        docs, auth_fail = poller.fetch_documents_page(since_cursor=fetch_since, page=page)
        if auth_fail:
            _log.warning("paperless_auth_failure source_id=%s", source.id)
            auth_failed = True
            break
        if not docs:
            break

        hard_stop = False
        for doc in docs:
            at_doc_cap = docs_done >= _MAX_PAPERLESS_DOCS_PER_TICK
            at_chunk_cap = chunks_used >= _MAX_PAPERLESS_CHUNKS_PER_TICK
            if at_doc_cap or at_chunk_cap:
                hard_stop = True
                break

            stored = _fetch_poll_cursor(source.id)
            if stored and doc.added < stored:
                _log.warning(
                    "paperless_added_before_cursor source_id=%s doc_added=%r stored=%r",
                    source.id,
                    doc.added,
                    stored,
                )
                docs_done += 1
                continue

            ext_id = str(int(doc.id))
            try:
                est_chunks = len(chunk_text(doc.content))
            except Exception:
                est_chunks = 1
            if chunks_used + est_chunks > _MAX_PAPERLESS_CHUNKS_PER_TICK:
                hard_stop = True
                break

            try:
                result = ingest_external_document(
                    user_id=source.user_id,
                    source_id=str(source.id),
                    external_kind="paperless",
                    external_document_id=ext_id,
                    content=doc.content,
                    poll_watermark=doc.added,
                    # LUM-724: the library lists these now, so they need a name.
                    title=getattr(doc, "title", "") or "",
                    stored_source_poll_cursor=stored,
                )
            except Exception as exc:
                _log.error(
                    "paperless_ingest_failed source_id=%s doc_id=%s type=%s msg=%s",
                    source.id,
                    ext_id,
                    type(exc).__name__,
                    str(exc)[:200],
                )
                hard_stop = True
                break

            chunks_used += int(result.chunk_count or 0)
            # Blocked-high external ingest returns ``advance_external_poll_cursor=False``
            # without ``skipped=True`` (see ``ingest_external_document``). If we kept
            # polling, a later row could monotonically advance ``poll_cursor`` past this
            # document's ``added`` while it was never reconciled — strict ``added__gt``
            # would then drop it permanently.
            if not result.advance_external_poll_cursor and not result.skipped:
                hard_stop = True
                break

            docs_done += 1

        if hard_stop:
            break
        page += 1

    # LUM-837: record health for paperless as well as IMAP. A bad token is the
    # other way this source can be permanently dead while looking polled.
    _record_poll_health(source.id, ok=not auth_failed, error_code="auth" if auth_failed else None)
    _update_poll_timestamp(source)
    _log.info(
        "paperless_poll_done source_id=%s docs_touched=%s chunks_est=%s",
        source.id,
        docs_done,
        chunks_used,
    )


def _build_adapter(source: SourceConfig):
    """Instantiate the appropriate adapter for source.source_type."""
    try:
        if source.source_type == "rss":
            from adapters.rss_source import RSSSource

            return RSSSource(source)
        if source.source_type == "page":
            from adapters.page_scraper import PageScraper

            return PageScraper(source)
        if source.source_type == "playwright":
            from adapters.playwright_fetcher import PlaywrightFetcher

            return PlaywrightFetcher(source)
        if source.source_type == "caldav":
            from adapters.calendar_adapter import CalendarAdapter

            return CalendarAdapter(source)
        _log.warning("Unknown source_type %r for source %s", source.source_type, source.id)
    except Exception as exc:
        _log.error("Adapter init error for source %s: %s", source.id, exc)
    return None


def _is_duplicate(url: str, user_id: str) -> bool:
    """Check if a signal with this URL already exists for this user."""
    if not url:
        return False
    try:
        ms = config.get_metadata_store()
        row = ms.fetch_one(
            "SELECT 1 FROM signals WHERE url = %s AND user_id = %s LIMIT 1",
            (url, user_id),
        )
        return row is not None
    except Exception:
        return False


def _persist_poll_cursor(source_id: str, cursor: str) -> None:
    """Persist the IMAP ``{uidvalidity}:{last_uid}`` cursor for a source (LUM-170).

    Unconditional set (not a lexical ``poll_cursor < %s`` comparison like
    ``ingest_external_document`` uses for ISO watermarks — the IMAP cursor is a
    compound, non-lexically-monotonic string; the adapter has already computed
    the correct advanced value).
    """
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE sources SET poll_cursor = %s WHERE id = %s",
            (cursor, source_id),
        )
    except Exception as exc:
        _log.debug("Could not persist poll_cursor for %s: %s", source_id, exc)


# ---- import-scope + backfill state (LUM-644) --------------------------------

_IMPORT_COLS = (
    "import_scope, import_since, import_before, import_state, "
    "backfill_cursor, backfill_max_uid, backfill_uidvalidity"
)


def _fetch_import_row(source_id: str) -> dict:
    """Per-tick read of a source's import/backfill columns (LUM-644).

    Returns ``{}`` for a legacy source (all NULL) or on error — the driver then
    skips seeding/backfill and the existing NULL-cursor behaviour is unchanged.
    """
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: this SELECT is keyed by the source's own primary-key id
        # (not a user_id filter); the caller already owns the source row.
        row = ms.fetch_one(f"SELECT {_IMPORT_COLS} FROM sources WHERE id = %s", (source_id,))
    except Exception:
        return {}
    return dict(row) if row else {}


def _set_import_state(source_id: str, state: str) -> None:
    try:
        ms = config.get_metadata_store()
        ms.execute("UPDATE sources SET import_state = %s WHERE id = %s", (state, source_id))
    except Exception as exc:
        _log.debug("Could not set import_state for %s: %s", source_id, exc)


def _resolve_import_since(scope: str | None, stored_since):
    """The SINCE lower bound for a scope. last_7d/last_30d compute it (row is NULL);
    custom carries an explicit ``import_since``; anything else has no lower bound."""
    if scope == "last_7d":
        return date.today() - timedelta(days=7)
    if scope == "last_30d":
        return date.today() - timedelta(days=30)
    return stored_since


def _start_backfill(source_id: str, *, since, max_uid: int, uidvalidity: int) -> None:
    """Enqueue a bounded backfill: cursor at 0, upper bound at the seed-time max UID.

    Persists the resolved ``import_since`` so the driver's per-tick slice reads a
    concrete lower bound (last_7d/last_30d store NULL at create time).
    """
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE sources SET import_state = 'backfilling', import_since = %s, "
            "backfill_cursor = 0, backfill_max_uid = %s, backfill_uidvalidity = %s "
            "WHERE id = %s",
            (since, max_uid, uidvalidity, source_id),
        )
    except Exception as exc:
        _log.debug("Could not start backfill for %s: %s", source_id, exc)


def _run_cleanup_tick(source) -> None:
    """LUM-646: advance the source's active inbox-cleanup job by one bounded step
    (backfill / auto-label / execute / spam-walk / undo). Early-returns when no job is
    active. Never raises — a failure here must not break the poll loop."""
    try:
        from services import email_cleanup

        email_cleanup.run_cleanup_tick(source)
    except Exception as exc:  # noqa: BLE001 - best-effort; never break the poll
        _log.warning("email_cleanup_tick_error source_id=%s type=%s", source.id, type(exc).__name__)


def _run_organize_tick(source) -> None:
    """LUM-645: one bounded organize tick for an opt-in email source. Undo takes
    priority when a reversal is pending, else the standing reconcile sweep runs.
    Never raises — a failure here must not break the poll loop."""
    try:
        from services import email_organize

        # Poll loop runs in a separate process from the API worker — opt-in cache
        # invalidation from PATCH/undo routes does not reach us; always read DB here.
        label_on, folder_on, _slugs, since = email_organize._opt_in_for(
            source.id,
            bypass_cache=True,
        )
        if since is None:
            return  # never opted in — no organize/undo work possible (cached, zero DB)
        undo = email_organize.undo_tick(source_id=source.id, user_id=source.user_id)
        if undo.get("remaining") or undo.get("reversed") or undo.get("failed"):
            return  # an undo sweep is in progress; skip reconcile this tick
        if label_on or folder_on:
            email_organize.reconcile_tick(source_id=source.id, user_id=source.user_id)
    except Exception as exc:  # noqa: BLE001 - best-effort; never break the poll
        _log.warning(
            "email_organize_tick_error source_id=%s type=%s", source.id, type(exc).__name__
        )


def _persist_backfill_cursor(source_id: str, cursor: int) -> None:
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE sources SET backfill_cursor = %s WHERE id = %s",
            (cursor, source_id),
        )
    except Exception as exc:
        _log.debug("Could not persist backfill_cursor for %s: %s", source_id, exc)


def _run_backfill_slice(source: SourceConfig, adapter, imp: dict) -> None:
    """One historical backfill slice. Additive; never touches ``poll_cursor``.

    Completion is an EMPTY search result (nothing left in the window at/below the
    upper bound) — NOT ``backfill_cursor >= backfill_max_uid``, which never
    terminates when out-of-window mail sits above the window (LUM-644 R2).
    """
    since = imp.get("import_since")
    before = imp.get("import_before")
    after_uid = int(imp.get("backfill_cursor") or 0)
    max_uid = int(imp.get("backfill_max_uid") or 0)
    bf_uidvalidity = imp.get("backfill_uidvalidity")
    if since is None or max_uid <= 0:
        _set_import_state(source.id, "done")
        return
    try:
        uidvalidity, signals, _advance, empty = adapter.backfill_slice(
            since=since, before=before, after_uid=after_uid, max_uid=max_uid
        )
        _record_connect_outcome(source.id, None)
    except Exception as exc:
        # LUM-633: the backfill runs AFTER the live poll in the same tick, so a
        # blocked connect here correctly overwrites the live poll's clear — the
        # mailbox is not healthy just because one of its two connects worked.
        _record_connect_outcome(source.id, exc)
        _log.warning(
            "email_backfill_error source_id=%s type=%s msg=%s",
            source.id,
            type(exc).__name__,
            str(exc)[:200],
        )
        return  # retry next tick; live poll already ran

    # UIDVALIDITY changed under the backfill → restart the bounded window under the
    # new validity (bounded, so a restart is bounded).
    if bf_uidvalidity is not None and uidvalidity != int(bf_uidvalidity):
        _log.warning(
            "email_backfill_uidvalidity_reset source_id=%s old=%s new=%s",
            source.id,
            bf_uidvalidity,
            uidvalidity,
        )
        try:
            new_uidvalidity, new_max = adapter.inbox_seed()
            _record_connect_outcome(source.id, None)
            _start_backfill(source.id, since=since, max_uid=new_max, uidvalidity=new_uidvalidity)
        except Exception as exc:
            _record_connect_outcome(source.id, exc)
            _log.warning(
                "email_backfill_reseed_error source_id=%s msg=%s", source.id, str(exc)[:120]
            )
        return

    if empty:
        _set_import_state(source.id, "done")
        _log.info("email_backfill_done source_id=%s", source.id)
        return

    safe = after_uid
    for raw in signals:
        uid = _email_uid_from_url(raw.url)
        if uid is None:
            continue
        if _is_duplicate(raw.url, source.user_id):
            safe = max(safe, uid)  # advance over duplicates (re-run progresses)
            continue
        try:
            # LUM-645: backfill mail must NOT fire the go-forward organize hook —
            # the standing reconcile sweep owns history (avoids flooding the pool).
            process_signal(raw, user_id=source.user_id, from_backfill=True)
            safe = max(safe, uid)
        except Exception as exc:
            _log.error(
                "email_backfill_persist_error source_id=%s uid=%s type=%s msg=%s",
                source.id,
                uid,
                type(exc).__name__,
                str(exc)[:200],
            )
            break
    if safe > after_uid:
        _persist_backfill_cursor(source.id, safe)


def _update_poll_timestamp(source: SourceConfig) -> None:
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE sources SET last_polled_at = %s WHERE id = %s",
            (datetime.now(timezone.utc), source.id),
        )
    except Exception as exc:
        _log.debug("Could not update last_polled_at for %s: %s", source.id, exc)


def _classify_construction_failure(exc: BaseException) -> str:
    """Code for a failure raised while *building* the adapter (LUM-633).

    Always returns a code — construction did fail, so "unknown" is not an
    option here. The default stays ``credential`` because that is what this
    site has always reported and what its tests pin; only conditions we can
    name by type get something more accurate.

    The mapping itself moved to :func:`services.email_credentials.
    classify_credential_failure` under LUM-759, because the SMTP opt-in route
    needs the same classification of the same raise surface and ADR 195 requires
    the two to agree. This wrapper keeps the name its four call sites use.
    """
    from services import email_credentials as _ec

    return _ec.classify_credential_failure(exc)


def _classify_connect_failure(exc: BaseException) -> str | None:
    """Code for a failure raised while *connecting*, or ``None`` (LUM-633).

    ``None`` means "this is not attributable to the connect" — and because
    connect always precedes fetch inside an adapter call, that in turn means
    the mailbox **did** connect. The caller clears health on ``None`` rather
    than leaving a stale error behind.

    Anything not named here returns ``None`` on purpose. `poll()` raises bare
    ``ValueError`` *after* connecting (``int()`` over server-supplied UIDVALIDITY
    and UID values), so a default of "network" would stamp fetch-phase garbage
    as a connection fault and make the mailbox flap red/green on one malformed
    message — which is what teaches people to ignore the indicator.

    **Every type here must agree with :func:`_classify_construction_failure`.**
    The two run at different sites on overlapping exception types; if they
    disagree, one mailbox condition gets two different stories depending only on
    which check observed it first.
    """
    from adapters.email_imap_source import ImapConnectError
    from services.egress_guard import EgressBlockedError

    from services import email_credentials as _ec

    if isinstance(exc, EgressBlockedError):  # covers ImapEgressProxyError
        return "egress_blocked"
    if isinstance(exc, _ec.ImapHostBlockedError):
        return "egress_blocked"
    if isinstance(exc, _ec.ImapHostUnresolvedError):
        return "network"
    if isinstance(exc, ImapConnectError):
        return "credential" if exc.kind == "auth" else "network"
    # `OAuthRefreshError` is deliberately absent, and that is not an oversight of
    # the "must agree" rule above. It is raised during *credential resolution* —
    # `email_credentials.load_connection` -> `email_oauth.get_access_token` — which
    # runs at the construction site, so it cannot reach this classifier. A branch
    # here would be dead code that a later reader would assume reachable and try to
    # test. If tokens ever start refreshing lazily *inside* connect, this comment
    # becomes wrong and the branch becomes necessary (LUM-757).
    return None


def _record_connect_outcome(source_id: str, exc: BaseException | None) -> None:
    """Stamp health for one connecting adapter call (LUM-633).

    The single entry point for every such call site, so the metadata poll, the
    full-ingest poll and the backfill cannot drift apart in how they report.
    ``exc=None`` means the call returned.
    """
    if exc is None:
        _record_poll_health(source_id, ok=True)
        return
    code = _classify_connect_failure(exc)
    if code is None:
        # Post-connect failure: reaching it proves the mailbox connected, so a
        # stale error must clear.
        _record_poll_health(source_id, ok=True)
    else:
        _record_poll_health(source_id, ok=False, error_code=code)


def _record_poll_health(source_id: str, *, ok: bool, error_code: str | None = None) -> None:
    """Persist per-source poll health (LUM-690/633) so a mailbox that can't
    currently authenticate is visible in the UI instead of only in logs — the
    `email_poll_skip code=credential` failure mode a soft-saved OAuth connect on
    an egress-proxied deployment can hit silently. ``ok=True`` clears any prior
    error (self-heals the next time the adapter can be constructed again).
    Best-effort; a health-write failure must never break the poll."""
    try:
        ms = config.get_metadata_store()
        if ok:
            ms.execute(
                "UPDATE sources SET last_poll_error_code = NULL, last_poll_error_at = NULL "
                "WHERE id = %s AND last_poll_error_code IS NOT NULL",
                (source_id,),
            )
        else:
            ms.execute(
                "UPDATE sources SET last_poll_error_code = %s, last_poll_error_at = %s "
                "WHERE id = %s",
                (error_code, datetime.now(timezone.utc), source_id),
            )
    except Exception as exc:
        _log.debug("Could not update poll health for %s: %s", source_id, exc)


def _row_to_source(row: dict) -> SourceConfig:
    # NOTE (LUM-844): ``page_monitor._row_to_source`` is a divergent copy
    # (``extraction_method`` default "trafilatura", ``poll_interval`` default
    # ``_DEFAULT_INTERVAL``); converge them when either is next edited.
    pc = row.get("poll_cursor")
    if pc == "":
        pc = None
    return SourceConfig(
        id=str(row["id"]),
        name=row["name"],
        source_type=row["source_type"],
        url=row["url"],
        category=row.get("category", ""),
        active=row["active"],
        poll_interval=row.get("poll_interval", 3600),
        extraction_method=row.get("extraction_method", "feedparser"),
        css_selector_override=row.get("css_selector_override"),
        last_polled_at=row.get("last_polled_at"),
        last_signal_at=row.get("last_signal_at"),
        poll_cursor=pc,
        user_id=row.get("user_id", "default"),
    )
