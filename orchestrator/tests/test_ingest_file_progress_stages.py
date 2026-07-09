# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-511: ingest_file on_progress stage order and graph gating."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


def _write_tmp(text: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(text)
        return f.name


def _run_ingest(monkeypatch, *, graph_mode: str, on_progress) -> None:
    from services.ingest import ingest_file

    path = _write_tmp("Hello ingest progress stages.\n")
    mock_ms = MagicMock()
    mock_ms.fetch_one.return_value = None
    mock_ms.execute.return_value = None
    mock_emb = MagicMock()
    mock_emb.embed_batch.return_value = [[0.0] * 768]
    mock_vs = MagicMock()

    monkeypatch.setattr("services.ingest.config.get_metadata_store", lambda: mock_ms)
    monkeypatch.setattr("services.ingest.config.get_vector_store", lambda: mock_vs)
    monkeypatch.setattr("services.ingest.config.get_embedder", lambda: mock_emb)
    monkeypatch.setattr("services.ingest.config.get_injection_scanner", lambda: MagicMock())
    monkeypatch.setattr(
        "services.ingest.config.get_extractors",
        lambda: {".txt": lambda p: open(p, encoding="utf-8").read()},
    )
    monkeypatch.setattr("services.ingest.config.get_graph_mode", lambda: graph_mode)
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
        with (
            patch("services.ingest.hooks.fire"),
            patch("services.ingest._emit_document_ingested_and_entities"),
        ):
            ingest_file(path, user_id="alice", on_progress=on_progress)
    finally:
        os.unlink(path)


@patch("services.entities.extract_entities", return_value=[])
def test_ingest_file_fires_stages_in_order(_mock_extract, monkeypatch) -> None:
    stages: list[str] = []

    def on_progress(stage: str, _pct: int | None, _msg: str | None) -> None:
        stages.append(stage)

    _run_ingest(monkeypatch, graph_mode="inprocess", on_progress=on_progress)
    assert stages == ["extracting", "chunking", "embedding", "graph"]


@patch("services.entities.extract_entities", return_value=[])
def test_ingest_file_omits_graph_stage_when_disabled(_mock_extract, monkeypatch) -> None:
    stages: list[str] = []

    def on_progress(stage: str, _pct: int | None, _msg: str | None) -> None:
        stages.append(stage)

    _run_ingest(monkeypatch, graph_mode="disabled", on_progress=on_progress)
    assert stages == ["extracting", "chunking", "embedding"]
    assert "graph" not in stages


@patch("services.entities.extract_entities", return_value=[])
@pytest.mark.parametrize("graph_mode", ["service", "inprocess"])
def test_ingest_file_includes_graph_stage_when_not_disabled(
    _mock_extract, monkeypatch, graph_mode: str
) -> None:
    stages: list[str] = []

    def on_progress(stage: str, _pct: int | None, _msg: str | None) -> None:
        stages.append(stage)

    _run_ingest(monkeypatch, graph_mode=graph_mode, on_progress=on_progress)
    assert stages[-1] == "graph"
