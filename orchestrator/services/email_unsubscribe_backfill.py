# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Populate the Subscriptions list from mail ingested before LUM-697 (LUM-711).

`email_unsubscribe.record_from_headers` is go-forward: a sender joins the list
the next time they write. Someone who imported thirty days of history at setup —
the case the import scopes exist for — sees an empty Subscriptions view until
each of those senders mails them again. For a monthly newsletter that is a month
of waiting for data already sitting in the mailbox.

**Read-only.** A `BODY.PEEK` header re-fetch, no body fetch, no mailbox writes.
`List-Unsubscribe` and `List-Unsubscribe-Post` are both already in the adapter's
`_HEADER_FIELDS`, so this costs one FETCH per source and nothing else.

Shaped after `email_sender_backfill` (LUM-657), deliberately: bounded, grouped
per source so each mailbox opens one connection, and **every failure skipped
rather than raised** — a changed `UIDVALIDITY`, a deleted message or an
unreachable mailbox is a skip, not a crash.

**No HTTP client, ever.** The `List-Unsubscribe` URI is written by whoever sent
the message; fetching it server-side is the Nextcloud Mail SSRF advisory
(CVE-2023-48307). `tests/test_unsubscribe_no_outbound_http.py` enforces that
structurally, and this module is inside its scope.
"""

from __future__ import annotations

import logging

import config

_log = logging.getLogger(__name__)

# Same default as the sender backfill: large enough to be useful in one pass,
# small enough that an operator running it blind cannot hammer a mailbox.
_DEFAULT_LIMIT = 500


def _parse_imap_url(url: str) -> tuple[str, int, int] | None:
    """``imap://{source_id}/{uidvalidity}/{uid}`` → ``(source_id, uidvalidity, uid)``."""
    if not url or not url.startswith("imap://"):
        return None
    try:
        source_id, uidvalidity, uid = url[len("imap://") :].rsplit("/", 2)
        return (source_id, int(uidvalidity), int(uid))
    except (ValueError, AttributeError):
        return None


def _known_senders(ms, user_id: str | None) -> set[tuple[str, str, str]]:
    """`(user_id, source_id, from_addr)` triples already in the Subscriptions list.

    **This is what makes a re-run safe.** `record_from_headers` increments
    `message_count` on every call, so replaying the same messages would inflate
    the count without bound. Skipping senders the list already knows keeps the
    job idempotent at sender granularity.

    The cost, stated rather than hidden: a sender the live path has already
    learned does **not** get its historical messages added to `message_count`.
    That count is a hint about how much mail a subscription generates, not a
    ledger — and a wrong-but-growing number would be worse than a low one.
    """
    conditions = []
    params: list = []
    if user_id is not None:
        conditions.append("user_id = %s")
        params.append(user_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = (
        ms.fetch_all(
            f"SELECT user_id, source_id, from_addr FROM email_unsubscribe_target {where}",
            tuple(params) if params else None,
        )
        or []
    )
    return {(str(r["user_id"]), str(r["source_id"]), str(r["from_addr"])) for r in rows}


def backfill_unsubscribe_targets(
    *,
    limit: int = _DEFAULT_LIMIT,
    user_id: str | None = None,
    source_id: str | None = None,
) -> dict:
    """Re-fetch `List-Unsubscribe` headers for historical email signals.

    Returns counts: ``{scanned, recorded, skipped, sources}``.
    """
    from adapters.email_imap_source import EmailImapSource
    from signals.feed_monitor import _row_to_source

    from services import email_unsubscribe

    ms = config.get_metadata_store()

    # Only signals that carry a sender: `record_from_headers` returns early on a
    # blank `from_addr` (the unique key would collapse every unparseable sender
    # into one runaway row), so fetching those would be pure waste. Signals
    # missing it entirely are `email_sender_backfill`'s job, not this one.
    conditions = ["url LIKE 'imap://%'", "from_addr <> ''"]
    params: list = []
    if user_id is not None:
        conditions.append("user_id = %s")
        params.append(user_id)
    if source_id is not None:
        conditions.append("source_id::text = %s")
        params.append(source_id)
    params.append(limit)
    rows = (
        ms.fetch_all(
            f"SELECT signal_id, source_id, url, user_id, from_addr, from_display "
            f"FROM signals WHERE {' AND '.join(conditions)} "
            f"ORDER BY created_at DESC LIMIT %s",
            tuple(params),
        )
        or []
    )

    known = _known_senders(ms, user_id)
    result = {"scanned": len(rows), "recorded": 0, "skipped": 0, "sources": 0}

    by_source: dict[str, list[dict]] = {}
    for r in rows:
        parsed = _parse_imap_url(str(r["url"]))
        if parsed is None:
            result["skipped"] += 1
            continue
        sid, uidvalidity, uid = parsed
        key = (str(r["user_id"]), sid, str(r["from_addr"]))
        if key in known:
            result["skipped"] += 1  # already in the list — see _known_senders
            continue
        by_source.setdefault(sid, []).append(
            {
                "uidvalidity": uidvalidity,
                "uid": uid,
                "user_id": str(r["user_id"]),
                "from_addr": str(r["from_addr"]),
                "from_display": str(r["from_display"] or ""),
            }
        )

    for src_id, candidates in by_source.items():
        src_row = ms.fetch_one(
            "SELECT id, name, source_type, url, category, active, poll_interval, "
            "extraction_method, css_selector_override, last_polled_at, last_signal_at, "
            "poll_cursor, user_id FROM sources WHERE id::text = %s",
            (src_id,),
        )
        if not src_row or src_row.get("source_type") != "imap":
            result["skipped"] += len(candidates)
            continue
        try:
            adapter = EmailImapSource(_row_to_source(src_row))
            current_uidvalidity, headers_by_uid = adapter.fetch_headers(
                [c["uid"] for c in candidates]
            )
        except Exception as exc:  # noqa: BLE001 — a bad source must not fail the run
            _log.warning(
                "email_unsubscribe_backfill: source %s skipped — %s: %s",
                src_id,
                type(exc).__name__,
                str(exc)[:200],
            )
            result["skipped"] += len(candidates)
            continue

        result["sources"] += 1
        for cand in candidates:
            if cand["uidvalidity"] != current_uidvalidity:
                result["skipped"] += 1  # UIDs reassigned — the stored uid is meaningless
                continue
            headers = headers_by_uid.get(cand["uid"])
            if not headers:
                result["skipped"] += 1  # message gone from the mailbox
                continue
            # Reuse the LIVE path's upsert rather than a second copy of the
            # preserve-on-empty rules — a backfill that blanked a target the
            # live path had already learned would make a sender silently
            # un-unsubscribable, which is the exact failure those rules exist
            # to prevent. It never raises.
            email_unsubscribe.record_from_headers(
                user_id=cand["user_id"],
                source_id=src_id,
                from_addr=cand["from_addr"],
                from_display=cand["from_display"],
                headers=headers,
            )
            result["recorded"] += 1

    _log.info("email_unsubscribe_backfill: %s", result)
    return result


if __name__ == "__main__":  # pragma: no cover — thin operator CLI
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill the Subscriptions list from historical mail (LUM-711)."
    )
    parser.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--source-id", default=None)
    args = parser.parse_args()
    print(
        backfill_unsubscribe_targets(
            limit=args.limit, user_id=args.user_id, source_id=args.source_id
        )
    )
