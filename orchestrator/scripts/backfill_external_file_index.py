#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Backfill `file_index` rows for documents ingested before LUM-724.

Both external ingest paths now write a `file_index` row, so `/documents` lists
them and `purge_document` can remove them. Everything ingested **before** this
landed has no such row and stays invisible in the library — this repairs that.

**An operator script, not a route, and deliberately so.** It necessarily crosses
every `user_id` — that is what makes it a backfill — so it must never be
reachable over HTTP. Run it from inside the container with the same environment
the migration runner uses::

    python scripts/backfill_external_file_index.py --dry-run
    python scripts/backfill_external_file_index.py

**Idempotent.** It uses the same UPSERT the live ingest paths use, including the
`WHERE published_from IS NULL` arbiter predicate that the PARTIAL unique index
(migration 046) requires — without it Postgres cannot infer the arbiter and the
statement fails on the first row.

**It backfills only documents that actually exist.** Two predicates carry that,
and both were review findings rather than first-draft text:

* ``skipped_reason IS NULL AND reversed_at IS NULL`` on the attachment ledger. A
  skip never created a document, and since LUM-727 ``declined`` can be the
  majority of rows — without this predicate the backfill would file everything
  the user said **no** to, inverting LUM-727 across the entire existing corpus in
  a one-way script.
* ``purged_at IS NULL`` on both ledgers. Purge retains and marks the row, so a
  re-run would otherwise resurrect every document the user has ever deleted.

It does **not** touch `updated_at` on rows that already exist and are unchanged,
because that column orders the library — a re-run would silently re-sort it.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_log = logging.getLogger("backfill_external_file_index")
logging.basicConfig(level=logging.INFO, format="[backfill] %(message)s")

# Only rows that produced a real, still-live document.
_ATTACHMENT_SQL = """
SELECT user_id, logical_path, content_sha256 AS file_hash, filename, chunk_count
  FROM email_attachment_ingest_log
 WHERE skipped_reason IS NULL
   AND reversed_at IS NULL
   AND purged_at IS NULL
   AND logical_path IS NOT NULL
"""

_EXTERNAL_SQL = """
SELECT user_id, logical_path, content_hash AS file_hash, chunk_count, external_id
  FROM external_documents
 WHERE purged_at IS NULL
   AND logical_path IS NOT NULL
"""


def _already_indexed(ms, user_id: str, logical_path: str) -> bool:
    row = ms.fetch_one(
        "SELECT 1 FROM file_index WHERE user_id = %s AND file_path = %s AND published_from IS NULL",
        (user_id, logical_path),
    )
    return row is not None


def run(*, dry_run: bool) -> int:
    import config
    from services import external_document_index as ext_index

    ms = config.get_metadata_store()
    written = 0
    skipped = 0
    per_user: dict[str, int] = {}

    for sql, kind in ((_ATTACHMENT_SQL, "email"), (_EXTERNAL_SQL, "paperless")):
        try:
            rows = ms.fetch_all(sql) or []
        except Exception as exc:  # noqa: BLE001 - an absent table is not fatal
            _log.warning("%s ledger unreadable (%s); skipping", kind, exc)
            continue

        _log.info("%-9s ledger: %d candidate row(s)", kind, len(rows))
        for r in rows:
            user_id = str(r["user_id"])
            logical_path = str(r["logical_path"])

            # Skipping an existing row is what keeps `updated_at` — and therefore
            # the library's sort order — stable across re-runs.
            if _already_indexed(ms, user_id, logical_path):
                skipped += 1
                continue

            if dry_run:
                _log.info("would write %s", logical_path)
                written += 1
                per_user[user_id] = per_user.get(user_id, 0) + 1
                continue

            name = (
                str(r.get("filename") or "")
                if kind == "email"
                else f"Paperless document {r.get('external_id')}"
            )
            if ext_index.record(
                user_id=user_id,
                logical_path=logical_path,
                file_hash=str(r.get("file_hash") or ""),
                file_type=ext_index.file_type_for(name),
                chunk_count=int(r.get("chunk_count") or 0),
                display_name=name,
                ocr_used=(kind == "paperless"),
            ):
                written += 1
                per_user[user_id] = per_user.get(user_id, 0) + 1

    verb = "would write" if dry_run else "wrote"
    _log.info("%s %d row(s); %d already indexed", verb, written, skipped)
    for user_id, n in sorted(per_user.items()):
        _log.info("  %-24s %d", user_id, n)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written and change nothing",
    )
    args = ap.parse_args()
    return run(dry_run=bool(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
