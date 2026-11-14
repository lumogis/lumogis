# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""`file_index` rows for externally-ingested documents (LUM-724).

Both external ingest paths — paperless (`ingest_external_document`) and email
attachments (`ingest_email_attachment`) — wrote Qdrant chunks plus their own
private ledger and **no `file_index` row**. Since `list_documents` reads
`file_index` and nothing else, both were *searchable but not browsable*; and
since `purge_document` selects its row from `file_index` and returns early when
absent, a paperless document had **no delete path at all**.

**One module because the two paths must not drift.** The defect being fixed is
precisely that they each drifted from `ingest_file` in the same way, separately.
A second copy of this logic is how that happens again.

The origin of a document is **derived from its path scheme, never stored**. The
logical path (`paperless://…`, `email-attachment://…`) is already the stable key
that the Qdrant payload, the KG evidence rows and purge all use, so a separate
`origin` column would be a second source of truth able to disagree with it.
"""

from __future__ import annotations

import logging
from typing import Optional

import config

_log = logging.getLogger(__name__)

#: Path prefixes that mark a document as externally ingested. A filesystem path
#: never starts with one — `ingest_file` resolves to an absolute path.
PAPERLESS_SCHEME = "paperless://"
EMAIL_ATTACHMENT_SCHEME = "email-attachment://"
_EXTERNAL_SCHEMES = (PAPERLESS_SCHEME, EMAIL_ATTACHMENT_SCHEME)


def is_external_path(file_path: str) -> bool:
    """True when this document came from an upstream store rather than disk.

    A **prefix** test, not a substring one: a real uploaded file could contain
    `://` somewhere in its name, and treating that as external would send a
    genuine local document down the paperless branch.
    """
    return str(file_path or "").startswith(_EXTERNAL_SCHEMES)


def origin_of(file_path: str) -> str:
    """`paperless` | `email` | `upload`. Drives the badge and the actions."""
    path = str(file_path or "")
    if path.startswith(PAPERLESS_SCHEME):
        return "paperless"
    if path.startswith(EMAIL_ATTACHMENT_SCHEME):
        return "email"
    return "upload"


#: What a caller is told when they try to share an external document.
SHARE_REFUSAL = (
    "Documents from paperless or email can't be shared with your household yet — "
    "their original system can remove them, and a shared copy would outlive that."
)


def refuse_share_if_external(file_path: str) -> None:
    """Raise ``ValueError`` for an external document. Callers map it to 409.

    **A pure predicate, deliberately, because there are THREE publish doors** and
    they live in two different route modules:

    * ``POST /api/v1/documents/{id}/publish`` — no size branch at all, and the
      one the web ShareToggle actually calls;
    * ``POST /scope/files/{id}/publish`` **above** the inline-chunk threshold —
      delegates to ``share_document``;
    * the same route **at or below** the threshold — projects inline via
      ``_publish_one`` and never reaches ``share_document``.

    Guarding only ``share_document`` covers one and a half of the three, and the
    uncovered path is the common one for external documents, which are small. A
    pure function keeps the guard callable from `services/` and from `routes/`
    without a `services/` → `routes/` import.

    Why refuse at all: LUM-157 sharing projects a second `file_index` row plus a
    copy of the chunks. For a paperless document the upstream owner can delete
    the original at any time, and the Phase 4 reconciliation would then have to
    retract a projection another household member is reading. Refusing is
    reversible; a shared-then-retracted document is not.
    """
    if is_external_path(file_path):
        raise ValueError(SHARE_REFUSAL)


def file_type_for(name: str) -> str:
    """Extension without the dot, lowercased — matching what `ingest_file` stores.

    `ingest_file` stores `ext` (e.g. `pdf`), so external rows must use the same
    shape or every consumer that groups or filters by type sees a second
    vocabulary. `''` when there is no extension, which `ingest_file` also stores
    for extensionless files — so no consumer meets a new value.
    """
    tail = str(name or "").rsplit(".", 1)
    if len(tail) != 2 or not tail[1]:
        return ""
    return tail[1].strip().lower()[:16]


def record(
    *,
    user_id: str,
    logical_path: str,
    file_hash: str,
    file_type: str,
    chunk_count: int,
    display_name: str,
    ocr_used: bool = False,
    strict: bool = False,
) -> Optional[int]:
    """Upsert the `file_index` row for an externally-ingested document.

    **An UPSERT, not an INSERT.** Three ordinary flows write the same logical
    path twice: undo→re-file (LUM-721), a paperless document whose content
    changed upstream, and a re-run of the backfill. A plain INSERT makes each of
    those a unique-violation 500.

    **The `WHERE published_from IS NULL` arbiter predicate is required**, not
    decorative: `file_index_user_path_uniq` is PARTIAL (migration 046), and
    without a matching predicate Postgres cannot infer the arbiter and the
    statement fails outright.

    `DO NOTHING` would be worse than either: a re-ingest that wrote a different
    number of chunks would leave a stale `chunk_count`, so the listing would
    disagree with Qdrant permanently.

    Returns the row id, or ``None`` if the write failed — the caller must not
    treat that as fatal. The chunks are already in Qdrant at this point, so the
    document is searchable; a missing row means it is not *listed*, which is the
    pre-LUM-724 status quo and is repairable by the backfill script.
    """
    ms = config.get_metadata_store()
    try:
        row = ms.fetch_one(
            # SCOPE-EXEMPT: writes the caller's OWN personal row. External
            # documents are never projections, so `published_from` is always
            # NULL and the partial unique index applies.
            "INSERT INTO file_index "
            "(file_path, file_hash, file_type, chunk_count, ocr_used, user_id, "
            " scope, published_from, display_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'personal', NULL, %s) "
            "ON CONFLICT (user_id, file_path) WHERE published_from IS NULL "
            "DO UPDATE SET file_hash = EXCLUDED.file_hash, "
            "              file_type = EXCLUDED.file_type, "
            "              chunk_count = EXCLUDED.chunk_count, "
            "              ocr_used = EXCLUDED.ocr_used, "
            "              display_name = EXCLUDED.display_name, "
            "              updated_at = NOW() "
            "RETURNING id",
            (
                logical_path,
                file_hash,
                file_type,
                int(chunk_count),
                bool(ocr_used),
                user_id,
                display_name or None,
            ),
        )
    except Exception:  # noqa: BLE001 - see docstring; never fail the ingest
        if strict:
            # The paperless caller writes INSIDE its ledger+cursor transaction, so
            # a swallowed failure here would leave that transaction aborted and
            # the following statements failing anyway — and worse, would commit an
            # advanced `poll_cursor` with no `file_index` row, which is
            # permanently unrepairable because the document is never re-polled.
            # Let it raise so the whole transaction rolls back and the next poll
            # retries cleanly.
            raise
        _log.warning(
            "external_document_index: file_index write failed path=%s",
            logical_path,
            exc_info=True,
        )
        return None
    return int(row["id"]) if row else None


def remove(*, user_id: str, logical_path: str) -> bool:
    """Delete the `file_index` row for one external document.

    Called by undo (LUM-721), by purge, and by source teardown. Idempotent: a
    missing row returns ``False`` rather than raising, because every caller
    reaches here on paths that may already have run.
    """
    ms = config.get_metadata_store()
    try:
        row = ms.fetch_one(
            # SCOPE-EXEMPT: the caller's own row, keyed on their own user_id.
            "DELETE FROM file_index "
            "WHERE user_id = %s AND file_path = %s AND published_from IS NULL "
            "RETURNING id",
            (user_id, logical_path),
        )
    except Exception:  # noqa: BLE001
        _log.warning(
            "external_document_index: file_index delete failed path=%s",
            logical_path,
            exc_info=True,
        )
        return False
    return row is not None


def remove_for_source(*, user_id: str, logical_paths: list[str]) -> int:
    """Bulk removal for source teardown (D8). Returns rows removed.

    Exists because `purge_source_vectors` was written when neither external path
    wrote a `file_index` row — its docstring said so — and this change falsifies
    that premise. Without this, deleting a mailbox or paperless connector would
    leave **phantom library rows pointing at deleted vectors**: the exact inverse
    of the bug that function was added to fix.
    """
    removed = 0
    for path in logical_paths:
        if remove(user_id=user_id, logical_path=path):
            removed += 1
    return removed
