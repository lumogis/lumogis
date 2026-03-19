# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""ntfy notifier adapter.

Posts notifications to an ntfy server (https://ntfy.sh or self-hosted).
Maps importance_score (0.0–1.0) to ntfy priority levels:
  < 0.3  → low (1)
  0.3–0.7 → default (3)
  > 0.7  → high (4)
  > 0.9  → urgent (5)

Falls back gracefully if ntfy is unreachable — returns False without raising.
"""

import logging
import os

import httpx

_log = logging.getLogger(__name__)


class NtfyNotifier:
    def __init__(self) -> None:
        self._base_url = os.environ.get("NTFY_URL", "http://ntfy:80").rstrip("/")
        self._topic = os.environ.get("NTFY_TOPIC", "lumogis")
        self._token = os.environ.get("NTFY_TOKEN", "")

    def notify(self, title: str, message: str, priority: float) -> bool:
        """POST notification to ntfy. Returns True on success."""
        ntfy_priority = self._map_priority(priority)
        headers = {
            "Title": title,
            "Priority": str(ntfy_priority),
            "Tags": "lumogis,signal",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        url = f"{self._base_url}/{self._topic}"
        try:
            resp = httpx.post(url, content=message.encode(), headers=headers, timeout=5)
            if resp.status_code in (200, 201):
                _log.info("ntfy notification sent: %r (priority=%s)", title, ntfy_priority)
                return True
            _log.warning("ntfy returned %d for %r", resp.status_code, title)
            return False
        except Exception as exc:
            _log.warning("ntfy unreachable — notification skipped: %s", exc)
            return False

    @staticmethod
    def _map_priority(score: float) -> int:
        if score > 0.9:
            return 5  # urgent
        if score > 0.7:
            return 4  # high
        if score >= 0.3:
            return 3  # default
        return 1  # low
