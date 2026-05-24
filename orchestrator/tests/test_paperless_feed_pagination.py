# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression: paperless REST pagination must not advance ``added__gt`` mid-tick."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from adapters.paperless_source import PaperlessDocument
from adapters.paperless_source import PaperlessPoller
from models.signals import SourceConfig
from services.ingest import IngestResult


def test_paperless_poll_freezes_since_cursor_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``added__gt`` is strict; advancing the watermark between HTTP pages drops
    later pages when many rows share the same ``added`` timestamp (bulk import).
    """
    from signals import feed_monitor

    from services import ingest as ingest_mod
    from services import paperless_credentials as pwc

    monkeypatch.setenv("PAPERLESS_POLL_PAGE_SIZE", "2")

    cursors_seen: list[str | None] = []

    def fake_fetch(
        self: PaperlessPoller,
        *,
        since_cursor: str | None,
        page: int,
    ) -> tuple[list[PaperlessDocument], bool]:
        cursors_seen.append(since_cursor)
        same = "2024-01-01T00:00:00Z"
        if page == 1:
            return [
                PaperlessDocument(1, "aa", same),
                PaperlessDocument(2, "bb", same),
            ], False
        if page == 2:
            return [PaperlessDocument(3, "cc", same)], False
        return [], False

    def fake_ingest(**_kwargs: object) -> IngestResult:
        return IngestResult(
            file_path="paperless://00000000-0000-0000-0000-000000000001/documents/1",
            chunk_count=1,
            advance_external_poll_cursor=True,
        )

    monkeypatch.setattr(feed_monitor, "_fetch_poll_cursor", lambda _sid: None)
    monkeypatch.setattr(feed_monitor, "_update_poll_timestamp", lambda _source: None)

    def _conn(_uid: str) -> MagicMock:
        return MagicMock(base_url="http://paperless:8000", token="t")

    monkeypatch.setattr(pwc, "load_connection", _conn)
    monkeypatch.setattr(PaperlessPoller, "fetch_documents_page", fake_fetch)
    monkeypatch.setattr(ingest_mod, "ingest_external_document", fake_ingest)

    src = SourceConfig(
        id="550e8400-e29b-41d4-a716-446655440001",
        name="p",
        source_type="paperless",
        url="http://paperless:8000",
        category="",
        active=True,
        poll_interval=60,
        extraction_method="paperless_http",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=None,
        user_id="u1",
    )
    feed_monitor._poll_paperless_source(src)

    assert cursors_seen, "fetch_documents_page should have been called"
    first = cursors_seen[0]
    assert all(c == first for c in cursors_seen), (
        "since_cursor must stay fixed for every page in one poll tick; "
        f"got sequence={cursors_seen!r}"
    )
