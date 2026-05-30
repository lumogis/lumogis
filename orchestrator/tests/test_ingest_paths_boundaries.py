# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-397 C2 — multi-root search and path containment boundaries."""

from __future__ import annotations

import json

from services.path_containment import _resolved_path_under_any_root
from services.path_containment import _resolved_path_under_root


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


def test_put_two_container_paths_search_finds_second(monkeypatch, tmp_path):
    """File only under the second ingest root is discoverable via fuzzy search."""
    first = tmp_path / "data"
    second = tmp_path / "extra"
    first.mkdir()
    second.mkdir()
    only_second = second / "only-here-doc.pdf"
    only_second.write_text("x", encoding="utf-8")

    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(first), str(second)]))

    from services.search import fuzzy_filename_search

    hits = fuzzy_filename_search("only-here", limit=10)
    assert any(h["path"] == str(only_second) for h in hits)


def test_fuzzy_search_union_roots_finds_both(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "alpha-report.pdf").write_text("a", encoding="utf-8")
    (second / "beta-report.pdf").write_text("b", encoding="utf-8")

    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(first), str(second)]))
    monkeypatch.delenv("FILESYSTEM_ROOT", raising=False)

    from services.search import fuzzy_filename_search

    hits = fuzzy_filename_search("report", limit=10)
    paths = {h["path"] for h in hits}
    assert str(first / "alpha-report.pdf") in paths
    assert str(second / "beta-report.pdf") in paths


def test_fuzzy_search_first_root_wins_at_limit(monkeypatch, tmp_path):
    """Earlier roots fill the limit first (documented ordering)."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for i in range(5):
        (first / f"match-{i}.txt").write_text("x", encoding="utf-8")
    (second / "match-second-only.txt").write_text("x", encoding="utf-8")

    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(first), str(second)]))

    from services.search import fuzzy_filename_search

    hits = fuzzy_filename_search("match", limit=3)
    assert len(hits) == 3
    assert all(h["path"].startswith(str(first)) for h in hits)


def test_tools_read_file_respects_any_ingest_root(monkeypatch, tmp_path):
    first = tmp_path / "data"
    second = tmp_path / "extra"
    first.mkdir()
    second.mkdir()
    allowed = second / "allowed.txt"
    allowed.write_text("hello", encoding="utf-8")
    denied = tmp_path / "denied.txt"
    denied.write_text("no", encoding="utf-8")

    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(first), str(second)]))

    from services.tools import _read_file

    ok = json.loads(_read_file({"path": str(allowed)}, user_id="u1"))
    assert ok.get("content") == "hello"

    bad = json.loads(_read_file({"path": str(denied)}, user_id="u1"))
    assert "error" in bad
    assert "Access denied" in bad["error"]
