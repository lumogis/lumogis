# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Signal digest: sends a periodic summary of top signals via the configured notifier.

Enabled by default. Disable with SIGNAL_DIGEST_ENABLED=false.

Environment variables:
  SIGNAL_DIGEST_ENABLED   true (default) | false
  SIGNAL_DIGEST_INTERVAL  seconds between digests, default 86400 (daily)
  SIGNAL_DIGEST_COUNT     max signals to include, default 5
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
    signals = _fetch_top_signals()
    if not signals:
        _log.info("signal_digest: no signals in window, skipping")
        return

    count = len(signals)
    title = f"Signal digest — {count} item{'s' if count != 1 else ''}"
    message = _format_digest(signals)

    try:
        notifier = config.get_notifier()
        sent = notifier.notify(title, message, priority=0.5)
        if sent:
            _log.info("signal_digest: sent digest (%d signals)", count)
        else:
            _log.warning("signal_digest: notifier returned False — digest not delivered")
    except Exception as exc:
        _log.error("signal_digest: notifier error: %s", exc)


def _fetch_top_signals() -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(seconds=_INTERVAL)
    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(
            "SELECT title, url, content_summary, relevance_score, importance_score "
            "FROM signals "
            "WHERE created_at >= %s "
            "ORDER BY relevance_score DESC, importance_score DESC "
            "LIMIT %s",
            (since, _COUNT),
        )
        return list(rows)
    except Exception as exc:
        _log.warning("signal_digest: DB fetch error: %s", exc)
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
