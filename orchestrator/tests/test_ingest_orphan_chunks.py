# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression: ingest_file must drop stale Qdrant chunks after re-ingest shrinks."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock
from unittest.mock import patch

from services.point_ids import document_chunk_point_id


def _write_tmp(text: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        return f.name


@patch("services.ingest.hooks.fire")
@patch("services.ingest.hooks.fire_background")
@patch("services.ingest._emit_document_ingested_and_entities")
def test_ingest_file_deletes_orphan_chunks_on_force_reingest(
    _emit, _fire_bg, _fire, monkeypatch
) -> None:
    from services.ingest import ingest_file

    path = _write_tmp("short")
    user_id = "alice"
    deleted: list[str] = []

    mock_vs = MagicMock()

    def _delete(*, collection: str, id: str) -> None:
        deleted.append(id)

    mock_vs.delete.side_effect = _delete

    mock_ms = MagicMock()
    mock_ms.fetch_one.return_value = {
        "file_hash": "old-hash",
        "chunk_count": 3,
    }
    mock_ms.execute.return_value = None

    mock_emb = MagicMock()
    mock_emb.embed_batch.return_value = [[0.0] * 768]

    monkeypatch.setattr("services.ingest.config.get_metadata_store", lambda: mock_ms)
    monkeypatch.setattr("services.ingest.config.get_vector_store", lambda: mock_vs)
    monkeypatch.setattr("services.ingest.config.get_embedder", lambda: mock_emb)
    monkeypatch.setattr("services.ingest.config.get_injection_scanner", lambda: MagicMock())
    monkeypatch.setattr(
        "services.ingest.config.get_extractors",
        lambda: {".txt": lambda p: open(p, encoding="utf-8").read()},
    )
    monkeypatch.setattr(
        "services.ingest.sanitise_at_ingest",
        lambda text, **kw: {
            "text": text,
            "blocked_high": False,
            "injection_flagged": False,
            "pattern_hits": [],
            "max_severity": None,
        },
    )
    monkeypatch.setattr("services.ingest.chunk_text", lambda text: [text])

    try:
        ingest_file(path, user_id=user_id, force=True)
    finally:
        os.unlink(path)

    mock_vs.delete_where.assert_called_once()
    filt = mock_vs.delete_where.call_args.kwargs["filter"]
    assert filt == {
        "must": [
            {"key": "user_id", "match": {"value": user_id}},
            {"key": "file_path", "match": {"value": path}},
        ]
    }
    expected_legacy = {document_chunk_point_id(user_id, path, i) for i in range(3)}
    assert expected_legacy.issubset(set(deleted))


@patch("services.ingest.hooks.fire")
@patch("services.ingest.hooks.fire_background")
def test_ingest_file_clears_all_chunks_when_reingest_extracts_empty(
    _fire_bg, _fire, monkeypatch
) -> None:
    from services.ingest import ingest_file

    path = _write_tmp("")
    user_id = "alice"
    deleted: list[str] = []

    mock_vs = MagicMock()
    mock_vs.delete.side_effect = lambda *, collection, id: deleted.append(id)

    mock_ms = MagicMock()
    mock_ms.fetch_one.return_value = {
        "file_hash": "old-hash",
        "chunk_count": 2,
    }
    mock_ms.execute.return_value = None

    monkeypatch.setattr("services.ingest.config.get_metadata_store", lambda: mock_ms)
    monkeypatch.setattr("services.ingest.config.get_vector_store", lambda: mock_vs)
    monkeypatch.setattr(
        "services.ingest.config.get_extractors",
        lambda: {".txt": lambda p: open(p, encoding="utf-8").read()},
    )
    monkeypatch.setattr("services.ingest.chunk_text", lambda text: [])

    try:
        result = ingest_file(path, user_id=user_id, force=True)
    finally:
        os.unlink(path)

    assert result.chunk_count == 0
    assert result.skipped is False
    mock_vs.delete_where.assert_called_once()
    assert deleted == [
        document_chunk_point_id(user_id, path, 0),
        document_chunk_point_id(user_id, path, 1),
    ]
    mock_ms.execute.assert_called_once()


@patch("services.ingest.hooks.fire")
@patch("services.ingest.hooks.fire_background")
@patch("services.ingest._emit_document_ingested_and_entities")
def test_ingest_file_reingest_uses_delete_where_for_sparse_indices(
    _emit, _fire_bg, _fire, monkeypatch
) -> None:
    """block_ingest can leave chunk_count=1 while the only vector sits at index 2."""
    from services.ingest import ingest_file

    path = _write_tmp("replacement")
    user_id = "alice"

    mock_vs = MagicMock()
    mock_ms = MagicMock()
    mock_ms.fetch_one.return_value = {
        "file_hash": "old-hash",
        "chunk_count": 1,
    }
    mock_ms.execute.return_value = None

    mock_emb = MagicMock()
    mock_emb.embed_batch.return_value = [[0.0] * 768]

    monkeypatch.setattr("services.ingest.config.get_metadata_store", lambda: mock_ms)
    monkeypatch.setattr("services.ingest.config.get_vector_store", lambda: mock_vs)
    monkeypatch.setattr("services.ingest.config.get_embedder", lambda: mock_emb)
    monkeypatch.setattr("services.ingest.config.get_injection_scanner", lambda: MagicMock())
    monkeypatch.setattr(
        "services.ingest.config.get_extractors",
        lambda: {".txt": lambda p: open(p, encoding="utf-8").read()},
    )
    monkeypatch.setattr(
        "services.ingest.sanitise_at_ingest",
        lambda text, **kw: {
            "text": text,
            "blocked_high": False,
            "injection_flagged": False,
            "pattern_hits": [],
            "max_severity": None,
        },
    )
    monkeypatch.setattr("services.ingest.chunk_text", lambda text: [text])

    try:
        ingest_file(path, user_id=user_id, force=True)
    finally:
        os.unlink(path)

    mock_vs.delete_where.assert_called_once_with(
        collection="documents",
        filter={
            "must": [
                {"key": "user_id", "match": {"value": user_id}},
                {"key": "file_path", "match": {"value": path}},
            ]
        },
    )


@patch("services.ingest.hooks.fire")
@patch("services.ingest.hooks.fire_background")
@patch("services.ingest._emit_document_ingested_and_entities")
def test_ingest_file_clears_vectors_on_first_write_without_index_row(
    _emit, _fire_bg, _fire, monkeypatch
) -> None:
    """block_ingest partial first ingest can write vectors with no file_index row."""
    from services.ingest import ingest_file

    path = _write_tmp("retry-after-partial")
    user_id = "alice"

    mock_vs = MagicMock()
    mock_ms = MagicMock()
    mock_ms.fetch_one.return_value = None

    mock_emb = MagicMock()
    mock_emb.embed_batch.return_value = [[0.0] * 768]

    monkeypatch.setattr("services.ingest.config.get_metadata_store", lambda: mock_ms)
    monkeypatch.setattr("services.ingest.config.get_vector_store", lambda: mock_vs)
    monkeypatch.setattr("services.ingest.config.get_embedder", lambda: mock_emb)
    monkeypatch.setattr("services.ingest.config.get_injection_scanner", lambda: MagicMock())
    monkeypatch.setattr(
        "services.ingest.config.get_extractors",
        lambda: {".txt": lambda p: open(p, encoding="utf-8").read()},
    )
    monkeypatch.setattr(
        "services.ingest.sanitise_at_ingest",
        lambda text, **kw: {
            "text": text,
            "blocked_high": False,
            "injection_flagged": False,
            "pattern_hits": [],
            "max_severity": None,
        },
    )
    monkeypatch.setattr("services.ingest.chunk_text", lambda text: [text])

    try:
        ingest_file(path, user_id=user_id)
    finally:
        os.unlink(path)

    mock_vs.delete_where.assert_called_once_with(
        collection="documents",
        filter={
            "must": [
                {"key": "user_id", "match": {"value": user_id}},
                {"key": "file_path", "match": {"value": path}},
            ]
        },
    )
