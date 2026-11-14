# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Fill `email_body_text` for mail ingested before search existed (LUM-819).

The ingest write is go-forward: a message becomes searchable the next time it
arrives. Everything already in the mailbox — which is precisely the "find what I
dealt with three months ago" corpus this feature is justified by — stays
invisible until it is re-fetched once.

Shaped after `email_unsubscribe_backfill` (LUM-711): bounded, grouped per source
so each mailbox opens one connection, every failure skipped rather than raised.
Three things it does NOT share with that job, each learned the hard way:

* **It uses `fetch_body_bounded`, not `fetch_bodies`.** `fetch_bodies` selects
  INBOX only and caps at 50 per call, so it cannot reach mail that LUM-645's
  organize has moved into a folder — which is most of the mail worth finding.
  `fetch_body_bounded` reuses `_locate_message`, takes a `folder_hint`, and
  returns a *reason* instead of raising.

* **It records permanent failures in `email_body_text_dead`.** Without a
  negative marker the candidate query re-selects its own failures on every run;
  once they outnumber `--limit` the job scans `limit` dead rows, stores nothing,
  and reports cheerful counts forever. This table is what makes it terminate.

* **It refuses to touch a metadata-mode mailbox.** That is a privacy control,
  not an optimisation: those users explicitly opted out of body reading, and a
  backfill that ignores the mode reverses a stated choice retroactively across
  someone's entire mail history.

Operator-invoked, never automatic. It does not run on upgrade.
"""

from __future__ import annotations

import argparse
import logging
import os
import time

import config

_log = logging.getLogger(__name__)

_DEFAULT_LIMIT = 500

#: A large mailbox means thousands of fetches against providers that meter
#: exactly that. Being throttled or locked out is a worse outcome than a slow
#: backfill, so the default is deliberately unhurried.
_DEFAULT_PACE_SECONDS = 0.2

#: `fetch_body_bounded` reasons that will never succeed on a retry. Taken from
#: the adapter's actual vocabulary (`_BODY_REASON_FOR_FETCH_ERROR` plus its
#: literal returns): ok / mailbox_unreachable / not_found / too_large /
#: ambiguous / no_body. Everything NOT listed here — in practice
#: `mailbox_unreachable`, and any reason a future adapter adds — is transient,
#: which is the safe default: retrying costs one fetch, while wrongly retiring a
#: message costs it permanently.
_TERMINAL_REASONS = frozenset({"not_found", "too_large", "ambiguous", "no_body"})

#: After this many transient failures a candidate is treated as dead anyway,
#: so a permanently flaky message cannot pin the job forever.
_MAX_ATTEMPTS = 3


def _pace_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("EMAIL_BODY_BACKFILL_PACE_SECONDS", "")))
    except (TypeError, ValueError):
        return _DEFAULT_PACE_SECONDS


def _parse_imap_url(url: str) -> tuple[str, int, int] | None:
    """``imap://{source_id}/{uidvalidity}/{uid}`` → ``(source_id, uidvalidity, uid)``."""
    if not url or not url.startswith("imap://"):
        return None
    try:
        source_id, uidvalidity, uid = url[len("imap://") :].rsplit("/", 2)
        return (source_id, int(uidvalidity), int(uid))
    except (ValueError, AttributeError):
        return None


def _candidates(ms, *, limit: int, user_id: str | None, source_id: str | None) -> list[dict]:
    """Email signals with no body row and no dead marker, newest first.

    ``scope = 'personal'`` is required, not cosmetic: `project_signal` copies
    ``url`` and ``from_addr`` verbatim onto the shared projection row, so
    without it every shared message is re-fetched twice and gets a second
    ``email_body_text`` row whose lifecycle is decoupled from the original's
    cascade.

    Newest first because a run that dies halfway should have indexed the mail
    the user is most likely to search for.
    """
    clauses = [
        # ``%%`` because psycopg2 %-formats the query before Postgres sees it
        # and this statement is executed with a params tuple.
        "s.url LIKE 'imap://%%'",
        "s.scope = 'personal'",
        "NOT EXISTS (SELECT 1 FROM email_body_text b WHERE b.signal_id = s.signal_id)",
        # `attempts >= _MAX_ATTEMPTS` and not merely "has a dead row": a
        # transient failure records an attempt so a message that ALWAYS times
        # out cannot be re-selected forever, but it stays a candidate until it
        # has genuinely earned being given up on.
        "NOT EXISTS (SELECT 1 FROM email_body_text_dead d "
        "WHERE d.signal_id = s.signal_id AND d.attempts >= %s)",
    ]
    params: list = [_MAX_ATTEMPTS]
    if user_id:
        clauses.append("s.user_id = %s")
        params.append(user_id)
    if source_id:
        clauses.append("s.source_id = %s")
        params.append(source_id)
    params.append(limit)
    rows = ms.fetch_all(
        "SELECT s.signal_id, s.url, s.source_id, s.user_id, s.email_message_id "
        f"FROM signals s WHERE {' AND '.join(clauses)} "
        "ORDER BY s.created_at DESC LIMIT %s",
        tuple(params),
    )
    return list(rows or [])


def _mark_dead(ms, signal_id: str, reason: str, *, terminal: bool) -> None:
    """Record a failed fetch.

    A **terminal** reason lands at the attempt ceiling immediately — retrying a
    message the server says is gone is pointless. A **transient** one records a
    single attempt, so it is retried on the next run but cannot be retried
    without end.
    """
    attempts = _MAX_ATTEMPTS if terminal else 1
    # GREATEST on the conflict path too: a terminal reason discovered on a RETRY
    # must land at the ceiling, not merely add one to whatever was there.
    ms.execute(
        "INSERT INTO email_body_text_dead (signal_id, reason, attempts) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (signal_id) DO UPDATE SET "
        "attempts = GREATEST(email_body_text_dead.attempts + 1, EXCLUDED.attempts), "
        "reason = EXCLUDED.reason, last_try = NOW()",
        (signal_id, reason[:200], attempts),
    )


def _store_body(ms, signal_id: str, user_id: str, text: str) -> None:
    from services.email_quote_strip import strip_quotes
    from services.signal_processor import _MAX_BODY_TEXT_BYTES

    capped = text.encode("utf-8")[:_MAX_BODY_TEXT_BYTES]
    stored = capped.decode("utf-8", "ignore")
    # LUM-826 — strip AFTER the cap so the stripped copy derives from exactly
    # the text this row holds. Mirrors signal_processor._persist_body_text.
    ms.execute(
        "INSERT INTO email_body_text (signal_id, user_id, text, truncated, text_stripped) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (signal_id) DO NOTHING",
        (signal_id, user_id, stored, len(stored) < len(text), strip_quotes(stored)),
    )


def restrip_stored_bodies(*, limit: int = _DEFAULT_LIMIT, user_id: str | None = None) -> dict:
    """Recompute ``text_stripped`` for rows already stored (LUM-826).

    Migration 075 adds the column but cannot populate it — the stripper is
    Python, not SQL. Rows written before it keep ``text_stripped IS NULL``,
    which means "index the full text": correct, unchanged, and un-deduplicated
    until this runs.

    Needs **no network**, unlike :func:`backfill_email_bodies` — it re-reads
    text Postgres already holds. That is why it is a separate entry point
    rather than a flag on the fetching path: an operator re-running the
    stripper after a heuristic improvement should not be re-hitting IMAP.

    Rows where the stripper declines are written back as ``NULL``, so a
    later, better stripper sees them again rather than treating an earlier
    refusal as settled.
    """
    from services.email_quote_strip import strip_quotes

    ms = config.get_metadata_store()
    where = "WHERE text_stripped IS NULL"
    params: list = []
    if user_id:
        where += " AND user_id = %s"
        params.append(user_id)
    rows = ms.fetch_all(
        # SCOPE-EXEMPT: operator-invoked maintenance over stored message text;
        # `user_id` narrows it when supplied, and no caller identity exists.
        f"SELECT signal_id, text FROM email_body_text {where} ORDER BY created_at LIMIT %s",
        tuple(params) + (limit,),
    )

    scanned = 0
    stripped_n = 0
    for row in rows:
        scanned += 1
        stripped = strip_quotes(row.get("text") or "")
        if stripped is None:
            continue
        ms.execute(
            "UPDATE email_body_text SET text_stripped = %s WHERE signal_id = %s",
            (stripped, row["signal_id"]),
        )
        stripped_n += 1

    return {"scanned": scanned, "stripped": stripped_n, "declined": scanned - stripped_n}


def backfill_email_bodies(
    *,
    limit: int = _DEFAULT_LIMIT,
    user_id: str | None = None,
    source_id: str | None = None,
) -> dict:
    """Re-fetch and store message text for signals that predate search."""
    from adapters.email_imap_source import EmailImapSource
    from signals.feed_monitor import _row_to_source

    ms = config.get_metadata_store()
    result = {"scanned": 0, "stored": 0, "skipped": 0, "dead": 0, "sources": 0}
    pace = _pace_seconds()

    candidates = _candidates(ms, limit=limit, user_id=user_id, source_id=source_id)
    result["scanned"] = len(candidates)
    if not candidates:
        return result

    by_source: dict[str, list[dict]] = {}
    for row in candidates:
        parsed = _parse_imap_url(str(row.get("url") or ""))
        if not parsed:
            result["skipped"] += 1
            continue
        src_id, uidvalidity, uid = parsed
        by_source.setdefault(src_id, []).append(
            {
                "signal_id": str(row["signal_id"]),
                "user_id": str(row.get("user_id") or "default"),
                "uid": uid,
                "uidvalidity": uidvalidity,
                "message_id": str(row.get("email_message_id") or "") or None,
            }
        )

    for src_id, group in by_source.items():
        src_row = ms.fetch_one(
            "SELECT id, name, source_type, url, category, active, poll_interval, "
            "extraction_method, css_selector_override, last_polled_at, last_signal_at, "
            "poll_cursor, user_id FROM sources WHERE id::text = %s",
            (src_id,),
        )
        if not src_row or src_row.get("source_type") != "imap":
            result["skipped"] += len(group)
            continue

        # THE PRIVACY CONTROL. Same guard as `email_attachment_ingest`. A
        # metadata-mode mailbox opted out of body reading; downloading its
        # bodies here would reverse that choice retroactively across the user's
        # whole history. No adapter is constructed for such a source — a fetch
        # that discards its result has already read the mail.
        if str(src_row.get("extraction_method") or "") != "imap_full_ingest":
            result["skipped"] += len(group)
            _log.info("email_body_backfill: source %s skipped — metadata mode", src_id)
            continue

        try:
            adapter = EmailImapSource(_row_to_source(src_row))
        except Exception as exc:  # noqa: BLE001 — a bad source must not fail the run
            _log.warning(
                "email_body_backfill: source %s skipped — %s",
                src_id,
                type(exc).__name__,
            )
            result["skipped"] += len(group)
            continue

        result["sources"] += 1
        for cand in group:
            try:
                fetched = adapter.fetch_body_bounded(
                    uid=cand["uid"],
                    message_id=cand["message_id"],
                    uidvalidity=cand["uidvalidity"],
                )
            except Exception as exc:  # noqa: BLE001 — one bad message, not the run
                _log.warning(
                    "email_body_backfill: signal %s skipped — %s",
                    cand["signal_id"],
                    type(exc).__name__,
                )
                _mark_dead(ms, cand["signal_id"], type(exc).__name__, terminal=False)
                result["skipped"] += 1
                continue

            reason = getattr(fetched, "reason", "ok")
            text = getattr(fetched, "text", "") or ""
            if reason == "ok" and text:
                _store_body(ms, cand["signal_id"], cand["user_id"], text)
                result["stored"] += 1
            elif reason in _TERMINAL_REASONS:
                _mark_dead(ms, cand["signal_id"], reason, terminal=True)
                result["dead"] += 1
            else:
                # Classified by REASON alone. The earlier `or not text` made this
                # branch unreachable — every failing FetchedBody carries text=""
                # — so a single IMAP outage permanently retired up to --limit
                # messages that were merely temporarily unreachable.
                _mark_dead(ms, cand["signal_id"], reason, terminal=False)
                result["skipped"] += 1

            if pace > 0:
                time.sleep(pace)

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill stored message text for email search (LUM-819)."
    )
    parser.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--source-id", default=None)
    parser.add_argument(
        "--restrip",
        action="store_true",
        help=(
            "Recompute quoted-reply stripping for already-stored text (LUM-826). "
            "Local only — never contacts IMAP."
        ),
    )
    args = parser.parse_args(argv)

    if args.restrip:
        result = restrip_stored_bodies(limit=args.limit, user_id=args.user_id)
        print(  # noqa: T201 — operator-facing CLI
            "email_body_restrip: " + " ".join(f"{k}={v}" for k, v in result.items())
        )
        return 0

    result = backfill_email_bodies(limit=args.limit, user_id=args.user_id, source_id=args.source_id)
    print(  # noqa: T201 — operator-facing CLI
        "email_body_backfill: " + " ".join(f"{k}={v}" for k, v in result.items())
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
