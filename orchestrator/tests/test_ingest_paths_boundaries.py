# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-397 C2 — multi-root search and path containment boundaries."""

from __future__ import annotations

import json

import pytest
from services.path_containment import _resolved_path_under_any_root
from services.path_containment import _resolved_path_under_root
from tests.test_lum884_ownership import FileIndexStore

import config


@pytest.fixture
def store():
    """Visibility-aware ``file_index`` fake (see test_lum884_ownership)."""
    s = FileIndexStore(allows_shared={"u1": True})
    config._instances["metadata_store"] = s
    return s


def test_mcp_denies_prefix_sibling_path(tmp_path):
    """`/dataX` must not match root `/data` (naive startswith bug)."""
    root = tmp_path / "data"
    root.mkdir()
    sibling = tmp_path / "dataX"
    sibling.mkdir()
    file_in_sibling = sibling / "secret.txt"
    file_in_sibling.write_text("nope", encoding="utf-8")

    assert _resolved_path_under_root(file_in_sibling.resolve(), root.resolve()) is False


def test_resolved_path_under_root_allows_nested_file(tmp_path):
    root = tmp_path / "data"
    nested = root / "docs" / "a.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("ok", encoding="utf-8")
    assert _resolved_path_under_root(nested.resolve(), root.resolve()) is True


def test_resolved_path_under_any_root_union(tmp_path):
    first = tmp_path / "data"
    second = tmp_path / "extra"
    first.mkdir()
    second.mkdir()
    in_second = second / "b.txt"
    in_second.write_text("x", encoding="utf-8")
    assert _resolved_path_under_any_root(in_second.resolve(), [str(first), str(second)]) is True
    outside = tmp_path / "outside.txt"
    outside.write_text("y", encoding="utf-8")
    assert _resolved_path_under_any_root(outside.resolve(), [str(first), str(second)]) is False


def _seed(store, *paths, user_id="u1"):
    for p in paths:
        store.add(p, user_id)


def test_put_two_container_paths_search_finds_second(monkeypatch, tmp_path, store):
    """A row under the second ingest root is found by name (LUM-884: via file_index)."""
    first = tmp_path / "data"
    second = tmp_path / "extra"
    first.mkdir()
    second.mkdir()
    only_second = second / "only-here-doc.pdf"
    only_second.write_text("x", encoding="utf-8")
    _seed(store, only_second)

    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(first), str(second)]))

    from services.search import fuzzy_filename_search

    hits = fuzzy_filename_search("only-here", limit=10, user_id="u1")
    assert any(h["path"] == str(only_second) for h in hits)


def test_fuzzy_search_union_roots_finds_both(monkeypatch, tmp_path, store):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    a = first / "alpha-report.pdf"
    b = second / "beta-report.pdf"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    _seed(store, a, b)

    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(first), str(second)]))
    monkeypatch.delenv("FILESYSTEM_ROOT", raising=False)

    from services.search import fuzzy_filename_search

    hits = fuzzy_filename_search("report", limit=10, user_id="u1")
    paths = {h["path"] for h in hits}
    assert str(a) in paths
    assert str(b) in paths


def test_fuzzy_search_orders_by_path_and_honours_limit(monkeypatch, tmp_path, store):
    """Results are ordered by path and cut at the limit (was: first root wins)."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for i in range(5):
        f = first / f"match-{i}.txt"
        f.write_text("x", encoding="utf-8")
        _seed(store, f)
    s2 = second / "match-second-only.txt"
    s2.write_text("x", encoding="utf-8")
    _seed(store, s2)

    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(first), str(second)]))

    from services.search import fuzzy_filename_search

    hits = fuzzy_filename_search("match", limit=3, user_id="u1")
    assert len(hits) == 3
    assert [h["path"] for h in hits] == sorted(h["path"] for h in hits)
    assert all(h["path"].startswith(str(first)) for h in hits)


def test_tools_read_file_respects_any_ingest_root(monkeypatch, tmp_path, store):
    first = tmp_path / "data"
    second = tmp_path / "extra"
    first.mkdir()
    second.mkdir()
    allowed = second / "allowed.txt"
    allowed.write_text("hello", encoding="utf-8")
    denied = tmp_path / "denied.txt"
    denied.write_text("no", encoding="utf-8")
    _seed(store, allowed.resolve(), denied.resolve())

    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(first), str(second)]))

    from services.tools import _read_file

    ok = json.loads(_read_file({"path": str(allowed)}, user_id="u1"))
    assert ok.get("content") == "hello"

    bad = json.loads(_read_file({"path": str(denied)}, user_id="u1"))
    assert "error" in bad
    assert "Access denied" in bad["error"]
