# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""paperless-ngx REST poller (read-only, LUM-281 v0.1).

Uses ``Authorization: Token <token>`` per paperless-ngx API conventions.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx
from services.paperless_credentials import PaperlessConnection

_log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5
_BASE_DELAY_S = 1.0
_BACKOFF_FACTOR = 2.0
_JITTER_FRAC = 0.25


@dataclass(frozen=True)
class PaperlessDocument:
    """Normalised paperless document row for ingest."""

    id: int
    content: str
    added: str


class PaperlessPoller:
    """httpx-backed single-page / multi-page fetch for ``/api/documents/``."""

    def __init__(self, connection: PaperlessConnection) -> None:
        self._conn = connection
        self._base = connection.base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._conn.token}",
            "Accept": "application/json",
        }

    def _sleep_backoff(self, attempt: int) -> None:
        raw = _BASE_DELAY_S * (_BACKOFF_FACTOR**attempt)
        jitter = 1.0 + random.uniform(-_JITTER_FRAC, _JITTER_FRAC)
        time.sleep(min(30.0, max(0.05, raw * jitter)))

    def _request_json(self, method: str, path: str, *, params: dict[str, Any]) -> Any:
        url = f"{self._base}{path}"
        last_exc: object | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
                    resp = client.request(
                        method,
                        url,
                        params=params,
                        headers=self._headers(),
                    )
                if resp.status_code in (401, 403):
                    return {"__auth_error__": True, "status": resp.status_code}
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = resp.status_code
                    self._sleep_backoff(attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                last_exc = exc
                self._sleep_backoff(attempt)
                continue
        _log.error(
            "paperless_http_final_failure type=%s msg=%s",
            type(last_exc).__name__ if last_exc else "None",
            str(last_exc)[:200] if last_exc else "",
        )
        return None

    def fetch_documents_page(
        self,
        *,
        since_cursor: str | None,
        page: int,
    ) -> tuple[list[PaperlessDocument], bool]:
        """Return one page of documents oldest-first; bool = auth failure."""
        page_size = int(os.environ.get("PAPERLESS_POLL_PAGE_SIZE", "25") or "25")
        page_size = max(1, min(100, page_size))
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "ordering": "added",
        }
        if since_cursor:
            params["added__gt"] = since_cursor

        data = self._request_json("GET", "/api/documents/", params=params)
        if data is None:
            return [], False
        if isinstance(data, dict) and data.get("__auth_error__"):
            return [], True
        if not isinstance(data, dict):
            _log.warning("paperless unexpected_json_type type=%s", type(data).__name__)
            return [], False

        results = data.get("results")
        if not isinstance(results, list):
            _log.warning("paperless missing_results")
            return [], False

        out: list[PaperlessDocument] = []
        for row in results:
            if not isinstance(row, dict):
                continue
            try:
                rid = int(row.get("id"))
            except (TypeError, ValueError):
                _log.warning("paperless bad_document_id raw=%r", row.get("id"))
                continue
            content = row.get("content")
            if content is None:
                content_s = ""
            elif isinstance(content, str):
                content_s = content
            else:
                content_s = ""
            added = row.get("added")
            added_s = added if isinstance(added, str) else ""
            if not content_s.strip():
                _log.info(
                    "paperless_skip_empty_content doc_id=%s user_content_absent=True",
                    rid,
                )
                continue
            if not added_s:
                _log.warning("paperless_skip_missing_added doc_id=%s", rid)
                continue
            out.append(PaperlessDocument(id=rid, content=content_s, added=added_s))
        return out, False
