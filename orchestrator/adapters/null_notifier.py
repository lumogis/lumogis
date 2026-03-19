# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Null notifier — no-op implementation of the Notifier port.

Used when NOTIFIER_BACKEND=none (the default). Keeps the factory pattern
clean without conditional imports elsewhere.
"""

import logging

_log = logging.getLogger(__name__)


class NullNotifier:
    def notify(self, title: str, message: str, priority: float) -> bool:
        _log.debug("NullNotifier: dropped notification %r (priority=%.2f)", title, priority)
        return True
