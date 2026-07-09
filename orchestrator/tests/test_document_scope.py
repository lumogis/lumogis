# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for document scope resolution (LUM-175)."""

from __future__ import annotations

import pytest
from auth import UserContext
from services.document_scope import DocumentNotFoundError
from services.document_scope import resolve_document_file_path


class _MetaStore:
    def fetch_one(self, query: str, params: tuple | None = None):
        q = (query or "").lower()
        if "allows_shared" in q:
            return {"allows_shared": True}
        assert "id = %s" in query
        assert params is not None
        assert params[0] == 42
        assert params[1] == "alice"
        return {"file_path": "/data/shared/lease.pdf"}


class _EmptyMetaStore:
    def fetch_one(self, query: str, params: tuple | None = None):
        return None


def test_resolve_document_file_path_visible_shared(monkeypatch) -> None:
    import config as cfg

    user = UserContext(user_id="alice")
    monkeypatch.setattr(cfg, "get_metadata_store", lambda: _MetaStore())

    path = resolve_document_file_path(user, 42)
    assert path == "/data/shared/lease.pdf"


def test_resolve_document_file_path_foreign_raises(monkeypatch) -> None:
    import config as cfg

    user = UserContext(user_id="bob")
    monkeypatch.setattr(cfg, "get_metadata_store", lambda: _EmptyMetaStore())

    with pytest.raises(DocumentNotFoundError):
        resolve_document_file_path(user, 99)
