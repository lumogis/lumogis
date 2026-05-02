# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""ntfy notifier adapter.

Posts notifications to an ntfy server (https://ntfy.sh or self-hosted).
Maps importance_score (0.0–1.0) to ntfy priority levels:
  < 0.3  → low (1)
  0.3–0.7 → default (3)
  > 0.7  → high (4)
  > 0.9  → urgent (5)

Per-user credentials (ADR 018, rollout step 2): the delivery config
(server URL, topic, optional token) is resolved per call from
:func:`services.ntfy_runtime.load_ntfy_runtime_config` rather than
read once from process env at construction. ``NOTIFIER_BACKEND=ntfy``
remains the deployment switch that selects this adapter.

Falls back gracefully on any failure — :meth:`notify` returns ``False``
without raising. Domain failures (``connector_not_configured``,
``credential_unavailable``) are logged with a structured ``code`` so
operators can grep the logs without inventing an HTTP surface for
this background path.
"""

import logging

import httpx
from services.ntfy_runtime import load_ntfy_runtime_config

from services import connector_credentials as ccs

_log = logging.getLogger(__name__)


class NtfyNotifier:
    def notify(
        self,
        title: str,
        message: str,
        priority: float,
        *,
        user_id: str,
    ) -> bool:
        """POST notification to ntfy. Returns True on success."""
        try:
            cfg = load_ntfy_runtime_config(user_id)
        except ccs.ConnectorNotConfigured:
            _log.info(
                "ntfy: connector_not_configured user_id=%s title=%r — skipping",
                user_id,
                title,
            )
            return False
        except ccs.CredentialUnavailable:
            _log.warning(
                "ntfy: credential_unavailable user_id=%s title=%r — skipping",
                user_id,
                title,
            )
            return False

        ntfy_priority = self._map_priority(priority)
        headers = {
            "Title": title,
            "Priority": str(ntfy_priority),
            "Tags": "lumogis,signal",
        }
        if cfg["token"]:
            headers["Authorization"] = f"Bearer {cfg['token']}"

        url = f"{cfg['url']}/{cfg['topic']}"
        try:
            resp = httpx.post(url, content=message.encode(), headers=headers, timeout=5)
            if resp.status_code in (200, 201):
                _log.info(
                    "ntfy notification sent: %r (priority=%s, user_id=%s)",
                    title,
                    ntfy_priority,
                    user_id,
                )
                return True
            _log.warning(
                "ntfy returned %d for %r (user_id=%s)",
                resp.status_code,
                title,
                user_id,
            )
            return False
        except Exception as exc:
            _log.warning(
                "ntfy unreachable — notification skipped (user_id=%s): %s",
                user_id,
                exc,
            )
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
