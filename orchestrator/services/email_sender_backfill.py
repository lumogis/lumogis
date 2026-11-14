# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-657 — backfill the persisted email sender on historical signals.

LUM-642 began persisting ``from_addr`` / ``from_display`` on email signals, so
rows ingested before that render "Unknown sender". This job re-fetches the
``From`` header over IMAP for signals with an empty ``from_addr`` and a live
``imap://`` locator, and updates them in place.

**Safe by construction:** bounded (``limit``), resumable (only touches
``from_addr = ''`` rows, and the UPDATE re-asserts that), and every failure mode
is *skipped, not raised* — a source that won't connect, a changed
``UIDVALIDITY`` (UIDs reassigned), or a message that's gone from the mailbox all
leave the row untouched. Read-only IMAP (``BODY.PEEK``) — never sets ``\\Seen``.

Run:  ``python -m services.email_sender_backfill [--limit N] [--user-id U]``
"""

from __future__ import annotations

import logging

import config

_log = logging.getLogger(__name__)


def _parse_imap_url(url: str) -> tuple[str, int, int] | None:
    """``imap://{source_id}/{uidvalidity}/{uid}`` → ``(source_id, uidvalidity, uid)``."""
    if not url or not url.startswith("imap://"):
        return None
    try:
        source_id, uidvalidity, uid = url[len("imap://") :].rsplit("/", 2)
        return (source_id, int(uidvalidity), int(uid))
    except (ValueError, AttributeError):
        return None


def backfill_email_senders(*, limit: int = 500, user_id: str | None = None) -> dict:
    """Backfill ``from_addr``/``from_display`` on email signals missing them.

    Returns counts: ``{scanned, updated, skipped, sources}``.
    """
    from adapters.email_imap_source import EmailImapSource
    from services.signal_processor import _extract_sender
    from services.signal_processor import _extract_sender_name
    from signals.feed_monitor import _row_to_source

    ms = config.get_metadata_store()

    conditions = ["from_addr = ''", "url LIKE 'imap://%'"]
    params: list = []
    if user_id is not None:
        conditions.append("user_id = %s")
        params.append(user_id)
    params.append(limit)
    rows = ms.fetch_all(
        f"SELECT signal_id, source_id, url FROM signals "
        f"WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT %s",
        tuple(params),
    )

    # Group candidates by source so each mailbox is opened once.
    by_source: dict[str, list[tuple[str, int, int]]] = {}
    for r in rows:
        parsed = _parse_imap_url(r["url"])
        if parsed is None:
            continue
        sid, uidvalidity, uid = parsed
        by_source.setdefault(sid, []).append((str(r["signal_id"]), uidvalidity, uid))

    result = {"scanned": len(rows), "updated": 0, "skipped": 0, "sources": 0}

    for source_id, candidates in by_source.items():
        src_row = ms.fetch_one(
            "SELECT id, name, source_type, url, category, active, poll_interval, "
            "extraction_method, css_selector_override, last_polled_at, last_signal_at, "
            "poll_cursor, user_id FROM sources WHERE id::text = %s",
            (source_id,),
        )
        if not src_row or src_row.get("source_type") != "imap":
            result["skipped"] += len(candidates)
            continue
        try:
            adapter = EmailImapSource(_row_to_source(src_row))
            current_uidvalidity, headers_by_uid = adapter.fetch_headers(
                [uid for (_sid, _uv, uid) in candidates]
            )
        except Exception as exc:  # noqa: BLE001 — a bad source must not fail the whole run
            _log.warning(
                "email_sender_backfill: source %s skipped — %s: %s",
                source_id,
                type(exc).__name__,
                str(exc)[:200],
            )
            result["skipped"] += len(candidates)
            continue

        result["sources"] += 1
        for signal_id, uidvalidity, uid in candidates:
            if uidvalidity != current_uidvalidity:
                result["skipped"] += 1  # UIDs reassigned — stored uid no longer valid
                continue
            headers = headers_by_uid.get(uid)
            if not headers:
                result["skipped"] += 1  # message gone
                continue
            from_addr = _extract_sender(headers)
            from_display = _extract_sender_name(headers)
            if not from_addr and not from_display:
                result["skipped"] += 1
                continue
            try:
                # Re-assert ``from_addr = ''`` so a concurrent poll that already
                # filled it wins (idempotent / resumable).
                ms.execute(
                    "UPDATE signals SET from_addr = %s, from_display = %s "
                    "WHERE signal_id = %s AND from_addr = ''",
                    (from_addr, from_display, signal_id),
                )
                result["updated"] += 1
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "email_sender_backfill: update failed signal=%s — %s",
                    signal_id,
                    str(exc)[:120],
                )
                result["skipped"] += 1

    _log.info("email_sender_backfill: %s", result)
    return result


if __name__ == "__main__":  # pragma: no cover — thin operator CLI
    import argparse

    parser = argparse.ArgumentParser(description="Backfill persisted email sender (LUM-657).")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--user-id", default=None)
    args = parser.parse_args()
    print(backfill_email_senders(limit=args.limit, user_id=args.user_id))
