# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Proactive email surfacing scheduler — snooze re-surface + morning brief (LUM-648).

Two in-process APScheduler jobs on the existing ``config.get_scheduler()`` singleton
(no new scheduler, no new Docker service), mirroring ``signals/digest.py``:

* ``triage_snooze_scan`` (interval, default 900s) — finds due snoozes, **flips them
  ``snoozed → needs_you`` in one atomic guarded ``UPDATE … RETURNING``**, and emits a
  ``SIGNAL_NEEDS_YOU`` per affected user. Flipping *before* emit means a failed emit
  leaves the row correctly ``needs_you`` and the next tick does not re-ping (the flip
  already removed it from the snoozed set). The ``WHERE status='snoozed'`` guard makes
  it idempotent and immune to a re-snooze race between scan ticks.
* ``triage_morning_brief`` (cron, ``minute=5``) — once per hour, emits a
  ``SIGNAL_MORNING_BRIEF`` to each user whose *local* hour equals ``TRIAGE_BRIEF_HOUR``
  and whose needs-you email count is > 0 (silent when empty). ``minute=5`` clears the
  dispatcher's *inclusive* quiet-hours boundary at a common ``07:00`` end;
  ``misfire_grace_time=3600`` runs the hour's brief even if the process was down across
  the fire minute.

**Advisory-lock discipline (LUM-358 pattern):** each sweep holds a *dedicated*
``checkout_connection`` open for its whole run and takes ``pg_try_advisory_lock`` on it
— NOT on the shared metadata-store connection (whose ``_ensure_conn`` reconnect can drop
a session lock mid-sweep, as ``digest.py`` risks). The scan/brief SQL itself runs on the
normal store; Postgres advisory locks are cluster-wide keyed, so cross-connection mutual
exclusion still holds. Closing the checkout connection auto-releases the session lock even
if the explicit unlock is skipped. Grep the repo for ``pg_advisory`` before picking a new
key pair; ``8420607/40`` and ``/41`` are reserved here (digest is ``/39``).
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone

import config

_log = logging.getLogger(__name__)

_ENABLED = os.environ.get("TRIAGE_REMINDER_ENABLED", "true").lower() != "false"
_INTERVAL = int(os.environ.get("TRIAGE_REMINDER_INTERVAL", "900"))
_BRIEF_ENABLED = os.environ.get("TRIAGE_BRIEF_ENABLED", "true").lower() != "false"
_BRIEF_HOUR = int(os.environ.get("TRIAGE_BRIEF_HOUR", "7"))

# Stable salt + issue id; grep the repo for ``pg_advisory`` before picking a new pair.
ADVISORY_LOCK_KEY1 = 8420607
ADVISORY_LOCK_KEY2_SCAN = 40
ADVISORY_LOCK_KEY2_BRIEF = 41

_SCAN_JOB_ID = "triage_snooze_scan"
_BRIEF_JOB_ID = "triage_morning_brief"


def start() -> None:
    scheduler = config.get_scheduler()
    if not scheduler.running:
        return

    if _ENABLED:
        scheduler.add_job(
            _scan_due_snoozes,
            trigger="interval",
            seconds=_INTERVAL,
            id=_SCAN_JOB_ID,
            name="Triage snooze re-surface scan",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _log.info("triage_reminders: snooze scan scheduled every %ds", _INTERVAL)
    else:
        _log.info("triage_reminders: snooze scan disabled via TRIAGE_REMINDER_ENABLED=false")

    if _BRIEF_ENABLED:
        scheduler.add_job(
            _send_morning_briefs,
            trigger="cron",
            minute=5,
            id=_BRIEF_JOB_ID,
            name="Triage morning brief",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        _log.info("triage_reminders: morning brief scheduled hourly at minute 5")
    else:
        _log.info("triage_reminders: morning brief disabled via TRIAGE_BRIEF_ENABLED=false")


def stop() -> None:
    try:
        scheduler = config.get_scheduler()
        for job_id in (_SCAN_JOB_ID, _BRIEF_JOB_ID):
            job = scheduler.get_job(job_id)
            if job:
                job.remove()
    except Exception as exc:
        _log.debug("triage_reminders stop: %s", exc)


@contextmanager
def _sweep_lock(key2: int):
    """Hold a dedicated connection + session advisory lock for the sweep.

    Yields ``True`` when the lock was acquired (this worker owns the tick), ``False``
    when another worker holds it (no-op tick). The connection closes on exit —
    auto-releasing the session lock as defense-in-depth even if unlock is skipped.
    """
    ms = config.get_metadata_store()
    with ms.checkout_connection() as conn:
        acquired = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(%s::integer, %s::integer)",
                    (ADVISORY_LOCK_KEY1, key2),
                )
                row = cur.fetchone()
                acquired = bool(row and row[0])
            yield acquired
        finally:
            if acquired:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT pg_advisory_unlock(%s::integer, %s::integer)",
                            (ADVISORY_LOCK_KEY1, key2),
                        )
                except Exception as exc:
                    _log.warning("triage_reminders: advisory unlock failed key2=%s: %s", key2, exc)


# The snooze-flip statement, hoisted to a module constant so the real-Postgres test
# EXECUTES THIS EXACT STRING instead of a hand-copy. That duplication is why the bug
# below survived: the test carried a byte-identical copy, so it faithfully reproduced
# the defect and reported it as a test failure in a lane nobody ran.
#
# One atomic guarded statement: flip due snoozes AND return the emit worklist with the
# email copy fields in a single round-trip (folds the former per-signal SELECT —
# LUM-664). The visibility predicate mirrors `visible_filter`'s household union so a
# signal a user can no longer see never re-surfaces its sender/subject in a push
# (LUM-663). The WHERE guard keeps it idempotent + re-snooze-race-safe; RETURNING is
# the emit worklist (flip-before-emit). Parameterless — status/scope literals are fixed.
#
# `allows_shared` is read with a SCALAR SUBQUERY, not a LEFT JOIN. The join form
# (`FROM signals s LEFT JOIN users u ON u.id = st.user_id`) is a PLANNING error, not a
# data-dependent one: Postgres does not permit the UPDATE target (`st`) to be
# referenced inside the FROM clause's join condition —
#     ERROR: invalid reference to FROM-clause entry for table "st"
#     DETAIL: There is an entry for table "st", but it cannot be referenced from this
#             part of the query.
# so it threw on EVERY execution. `_scan_due_snoozes` catches and logs, so the job
# never crashed — it simply flipped nothing, forever, and no snoozed mail ever came
# back. The subquery keeps the LEFT-join-with-default semantics exactly: a user with
# no `users` row still defaults to TRUE via COALESCE.
FLIP_DUE_SNOOZES_SQL = (
    "UPDATE signal_triage st "
    "SET status = 'needs_you', snoozed_until = NULL, updated_at = NOW() "
    "FROM signals s "
    "WHERE st.status = 'snoozed' AND st.snoozed_until <= now() "
    "AND s.signal_id = st.signal_id "
    "AND ("
    "(s.scope = 'personal' AND s.user_id = st.user_id) "
    "OR s.scope = 'system' "
    "OR (s.scope = 'shared' AND COALESCE("
    # SCOPE-EXEMPT: `users` is the account table; this reads the triage row owner's
    # own `allows_shared` flag, which is the household-visibility rule itself.
    "(SELECT u.allows_shared FROM users u WHERE u.id = st.user_id), TRUE))"
    ") "
    "RETURNING st.user_id, st.signal_id, s.title, s.from_display, s.from_addr"
)


def _scan_due_snoozes() -> None:
    try:
        with _sweep_lock(ADVISORY_LOCK_KEY2_SCAN) as acquired:
            if not acquired:
                _log.debug("triage_reminders: snooze scan skipped, advisory lock not acquired")
                return
            _run_snooze_scan()
    except Exception as exc:
        _log.error("triage_reminders: snooze scan failed: %s", exc)


def _run_snooze_scan() -> None:
    ms = config.get_metadata_store()
    # One atomic guarded statement: flip due snoozes AND return the emit worklist
    # with the email copy fields in a single round-trip (folds the former per-signal
    # SELECT — LUM-664), joined to `signals` (+ `users` for allows_shared). The
    # visibility predicate mirrors `visible_filter`'s household union so a signal a
    # user can no longer see never re-surfaces its sender/subject in a push (LUM-663:
    # a stale snooze on a now-invisible shared signal simply is not flipped). The
    # WHERE guard keeps it idempotent + re-snooze-race-safe; RETURNING is the emit
    # worklist (flip-before-emit). Parameterless — status/scope literals are fixed.
    rows = ms.fetch_all(FLIP_DUE_SNOOZES_SQL, ())
    if not rows:
        return

    by_user: dict[str, list[dict]] = {}
    for r in rows:
        uid = r.get("user_id")
        sid = r.get("signal_id")
        if uid and sid:
            by_user.setdefault(str(uid), []).append(r)

    for user_id, signals in by_user.items():
        try:
            _emit_needs_you(user_id, signals)
        except Exception as exc:
            # Per-user isolation: the flip already persisted; a failed emit skips
            # one ping and never aborts the sweep.
            _log.error("triage_reminders: needs-you emit failed user_id=%s: %s", user_id, exc)


def _emit_needs_you(user_id: str, signals: list[dict]) -> None:
    """Emit one SIGNAL_NEEDS_YOU per user from the flip worklist rows.

    ``signals`` are RETURNING rows carrying ``signal_id`` + the email copy fields
    (``title``/``from_display``/``from_addr``) — no extra query needed (LUM-664).
    """
    from models.notifications import NotificationType
    from models.notifications import TypedNotification
    from services.notifications.dispatcher import emit
    from services.triage_surfacing import EMAIL_TARGET_PATH
    from services.triage_surfacing import needs_you_copy

    count = len(signals)
    if count == 1:
        row = signals[0]
        title, body = needs_you_copy(row, count=1)
        metadata = {
            "signal_id": str(row.get("signal_id")),
            "count": 1,
            "target_path": EMAIL_TARGET_PATH,
        }
    else:
        title, body = needs_you_copy({}, count=count)
        metadata = {"signal_id": None, "count": count, "target_path": EMAIL_TARGET_PATH}

    emit(
        TypedNotification(
            user_id=user_id,
            notification_type=NotificationType.SIGNAL_NEEDS_YOU,
            title=title,
            body=body,
            metadata=metadata,
        )
    )


def _send_morning_briefs() -> None:
    try:
        with _sweep_lock(ADVISORY_LOCK_KEY2_BRIEF) as acquired:
            if not acquired:
                _log.debug("triage_reminders: morning brief skipped, advisory lock not acquired")
                return
            _run_morning_briefs()
    except Exception as exc:
        _log.error("triage_reminders: morning brief failed: %s", exc)


def _run_morning_briefs() -> None:
    from models.notifications import NotificationType
    from models.notifications import TypedNotification
    from services.notifications.dispatcher import _resolve_timezone
    from services.notifications.dispatcher import emit
    from services.triage_surfacing import EMAIL_TARGET_PATH
    from services.triage_surfacing import brief_copy
    from services.triage_surfacing import count_needs_you_email

    user_ids = _enumerate_brief_users()
    # LUM-665: one batched timezone read instead of a per-user settings fetch — the
    # local-hour gate rejects ~23/24 users each run, so a per-user query is wasteful.
    tz_by_user = _load_brief_timezones()
    now_utc = datetime.now(timezone.utc)
    for user_id in user_ids:
        try:
            tz = _resolve_timezone({"timezone": tz_by_user.get(user_id)})
            if now_utc.astimezone(tz).hour != _BRIEF_HOUR:
                continue
            count = count_needs_you_email(user_id)
            if count <= 0:
                continue
            title, body = brief_copy(count)
            emit(
                TypedNotification(
                    user_id=user_id,
                    notification_type=NotificationType.SIGNAL_MORNING_BRIEF,
                    title=title,
                    body=body,
                    metadata={"count": count, "target_path": EMAIL_TARGET_PATH},
                )
            )
        except Exception as exc:
            _log.warning("triage_reminders: brief skipped user_id=%s: %s", user_id, exc)


def _enumerate_brief_users() -> list[str]:
    """Distinct user_ids that could have a needs-you brief.

    UNION of signal *owners* and triage-row *owners*: a household member who owns no
    sources of their own can still triage shared signals (needs_you / snooze) and
    must receive a brief. Returns ``[]`` on any DB error so the job never crashes.
    """
    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(
            "SELECT DISTINCT user_id FROM signals WHERE user_id IS NOT NULL "
            "UNION "
            "SELECT DISTINCT user_id FROM signal_triage WHERE user_id IS NOT NULL "
            "ORDER BY user_id",
            (),
        )
        return [r["user_id"] for r in rows if r.get("user_id")]
    except Exception as exc:
        _log.warning("triage_reminders: brief user enumeration failed: %s", exc)
        return []


def _load_brief_timezones() -> dict[str, str | None]:
    """One batched ``{user_id: timezone}`` read for the brief's local-hour gate.

    Users with no ``notification_user_settings`` row are simply absent from the map
    → the caller falls back to the dispatcher's UTC default. Returns ``{}`` on any DB
    error (every user then defaults to UTC — no crash).
    """
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: notification_user_settings is per-user settings (no scope column).
        rows = ms.fetch_all(
            "SELECT user_id, timezone FROM notification_user_settings WHERE user_id IS NOT NULL",
            (),
        )
        return {r["user_id"]: r.get("timezone") for r in rows if r.get("user_id")}
    except Exception as exc:
        _log.warning("triage_reminders: brief timezone batch load failed: %s", exc)
        return {}
