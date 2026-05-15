# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Signal digest: sends a periodic per-user summary of top signals via the configured notifier.

Enabled by default. Disable with SIGNAL_DIGEST_ENABLED=false.

Per-user fanout (ADR 018, ntfy migration): the digest used to be a
single household-global notification. With per-user connector
credentials each user owns their own ntfy topic/token, so the digest
now enumerates the distinct ``user_id`` values that produced signals
in the window and emits one notification per user. Users with zero
signals in the window get no notification (parity with the old
"no signals → skip" behavior, applied per user).

Environment variables:
  SIGNAL_DIGEST_ENABLED   true (default) | false
  SIGNAL_DIGEST_INTERVAL  seconds between digests, default 86400 (daily)
  SIGNAL_DIGEST_COUNT     max signals to include per user, default 5
"""

import logging
import os
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import config

_log = logging.getLogger(__name__)

_ENABLED = os.environ.get("SIGNAL_DIGEST_ENABLED", "true").lower() != "false"
_INTERVAL = int(os.environ.get("SIGNAL_DIGEST_INTERVAL", "86400"))
_COUNT = int(os.environ.get("SIGNAL_DIGEST_COUNT", "5"))

# Session advisory keys for `_send_digest` (see LUM-39). Keeps uvicorn
# ``--workers N`` fan-out to a single digest run per DB per tick — lock is
# per Postgres backend session / worker process.
# Stable salt + issue id; grep the repo for ``pg_advisory`` before picking a new pair.
ADVISORY_LOCK_KEY1 = 8420607
ADVISORY_LOCK_KEY2 = 39

_job_id = "signal_digest"


def start() -> None:
    if not _ENABLED:
        _log.info("signal_digest: disabled via SIGNAL_DIGEST_ENABLED=false")
        return

    scheduler = config.get_scheduler()
    if not scheduler.running:
        return

    scheduler.add_job(
        _send_digest,
        trigger="interval",
        seconds=_INTERVAL,
        id=_job_id,
        name="Signal digest",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _log.info("signal_digest: scheduled every %ds", _INTERVAL)


def stop() -> None:
    try:
        scheduler = config.get_scheduler()
        job = scheduler.get_job(_job_id)
        if job:
            job.remove()
    except Exception as exc:
        _log.debug("signal_digest stop: %s", exc)


def _send_digest() -> None:
    ms = config.get_metadata_store()
    try:
        got = ms.fetch_one(
            "SELECT pg_try_advisory_lock(%s::integer, %s::integer) AS ok",
            (ADVISORY_LOCK_KEY1, ADVISORY_LOCK_KEY2),
        )
    except Exception as exc:
        _log.warning("signal_digest: advisory lock try failed: %s", exc)
        return
    if not got or not got.get("ok"):
        _log.debug("signal_digest: skipped, advisory lock not acquired")
        return

    try:
        since = datetime.now(timezone.utc) - timedelta(seconds=_INTERVAL)
        user_ids = _fetch_active_user_ids(since)
        if not user_ids:
            _log.info("signal_digest: no signals in window, skipping")
            return

        notifier = config.get_notifier()
        for user_id in user_ids:
            signals = _fetch_top_signals_for_user(user_id, since)
            if not signals:
                continue

            count = len(signals)
            title = f"Signal digest — {count} item{'s' if count != 1 else ''}"
            message = _format_digest(signals)

            try:
                sent = notifier.notify(title, message, priority=0.5, user_id=user_id)
                if sent:
                    _log.info(
                        "signal_digest: sent digest user_id=%s count=%d",
                        user_id,
                        count,
                    )
                else:
                    _log.warning(
                        "signal_digest: notifier returned False — digest not delivered "
                        "(user_id=%s)",
                        user_id,
                    )
            except Exception as exc:
                _log.error(
                    "signal_digest: notifier error (user_id=%s): %s",
                    user_id,
                    exc,
                )
    finally:
        try:
            un = ms.fetch_one(
                "SELECT pg_advisory_unlock(%s::integer, %s::integer) AS ok",
                (ADVISORY_LOCK_KEY1, ADVISORY_LOCK_KEY2),
            )
            if un is not None and un.get("ok") is False:
                _log.warning("signal_digest: pg_advisory_unlock returned false (session mismatch)")
        except Exception as exc:
            _log.error("signal_digest: advisory unlock failed: %s", exc)


def _fetch_active_user_ids(since: datetime) -> list[str]:
    """Distinct ``user_id`` values that produced a signal in the window.

    Ordered for deterministic test/log output. Returns ``[]`` on any
    DB error so the digest never crashes the scheduler thread.
    """
    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(
            "SELECT DISTINCT user_id FROM signals "
            "WHERE created_at >= %s AND user_id IS NOT NULL "
            "ORDER BY user_id",
            (since,),
        )
        return [r["user_id"] for r in rows if r.get("user_id")]
    except Exception as exc:
        _log.warning("signal_digest: active-users fetch error: %s", exc)
        return []


def _fetch_top_signals_for_user(user_id: str, since: datetime) -> list[dict]:
    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(
            "SELECT title, url, content_summary, relevance_score, importance_score "
            "FROM signals "
            "WHERE created_at >= %s AND user_id = %s "
            "ORDER BY relevance_score DESC, importance_score DESC "
            "LIMIT %s",
            (since, user_id, _COUNT),
        )
        return list(rows)
    except Exception as exc:
        _log.warning(
            "signal_digest: per-user fetch error (user_id=%s): %s",
            user_id,
            exc,
        )
        return []


def _format_digest(signals: list[dict]) -> str:
    lines = []
    for i, s in enumerate(signals, 1):
        title = (s.get("title") or "").strip() or "(no title)"
        summary = (s.get("content_summary") or "").strip()
        url = (s.get("url") or "").strip()
        score = s.get("relevance_score") or s.get("importance_score") or 0.0

        parts = [f"{i}. {title}"]
        if summary:
            parts.append(f"   {summary[:140]}")
        if url:
            parts.append(f"   {url}")
        parts.append(f"   score: {score:.2f}")
        lines.append("\n".join(parts))

    return "\n\n".join(lines)
