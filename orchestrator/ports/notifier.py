"""Port: notifier protocol.

Implemented by ntfy_notifier and null_notifier.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    def notify(self, title: str, message: str, priority: float) -> bool:
        """Send a notification. Returns True on success, False on failure.

        priority: 0.0–1.0 importance_score passed from signal_processor.
        Implementations map this to their own priority scheme.
        """
        ...
