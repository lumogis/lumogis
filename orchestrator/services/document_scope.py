# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Resolve file_index.id to file_path for document-scoped chat (LUM-175)."""

from __future__ import annotations

from auth import UserContext
from visibility import visible_filter

import config


class DocumentNotFoundError(LookupError):
    """No visible file_index row exists for the requested document id."""


def resolve_document_file_path(user: UserContext, document_id: int) -> str:
    """Return canonical file_path for a library document visible to the user."""
    if document_id <= 0:
        raise ValueError("document_id must be positive")

    clause, params = visible_filter(user)
    row = config.get_metadata_store().fetch_one(
        f"SELECT file_path FROM file_index WHERE id = %s AND {clause}",
        (document_id, *params),
    )
    if not row or not row.get("file_path"):
        raise DocumentNotFoundError(document_id)
    return str(row["file_path"])
