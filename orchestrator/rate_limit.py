# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""In-process rate limit helpers shared by auth and public invite routes."""

from __future__ import annotations

import time
from collections import defaultdict
from collections import deque
from threading import Lock
from typing import Deque

RATE_WINDOW_SECONDS = 60.0
RATE_MAX_FAILURES = 5


def rate_check(
    store_a: dict[str, Deque[float]],
    key_a: str,
    store_b: dict[str, Deque[float]] | None,
    key_b: str | None,
    *,
    max_failures: int = RATE_MAX_FAILURES,
    window_seconds: float = RATE_WINDOW_SECONDS,
    lock: Lock,
) -> bool:
    """Return ``True`` when both buckets (if configured) are under the limit."""
    now = time.monotonic()
    cutoff = now - window_seconds
    with lock:
        stores: list[Deque[float]] = [store_a[key_a]]
        if store_b is not None and key_b is not None:
            stores.append(store_b[key_b])
        for store in stores:
            while store and store[0] < cutoff:
                store.popleft()
            if len(store) >= max_failures:
                return False
        return True


def rate_record_failure(
    store_a: dict[str, Deque[float]],
    key_a: str,
    store_b: dict[str, Deque[float]] | None,
    key_b: str | None,
    *,
    lock: Lock,
) -> None:
    now = time.monotonic()
    with lock:
        store_a[key_a].append(now)
        if store_b is not None and key_b is not None:
            store_b[key_b].append(now)


def rate_record_success(
    store_b: dict[str, Deque[float]],
    key_b: str,
    *,
    lock: Lock,
) -> None:
    """Clear the secondary bucket on success; primary keeps its history."""
    with lock:
        store_b[key_b].clear()


class FailureRateLimiter:
    """Dual-key failed-attempt limiter (IP + secondary key such as email)."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._ip: dict[str, Deque[float]] = defaultdict(deque)
        self._secondary: dict[str, Deque[float]] = defaultdict(deque)

    def check(self, key_ip: str, key_secondary: str) -> bool:
        return rate_check(
            self._ip,
            key_ip,
            self._secondary,
            key_secondary,
            lock=self._lock,
        )

    def record_failure(self, key_ip: str, key_secondary: str) -> None:
        rate_record_failure(self._ip, key_ip, self._secondary, key_secondary, lock=self._lock)

    def record_success(self, key_ip: str, key_secondary: str) -> None:
        rate_record_success(self._secondary, key_secondary, lock=self._lock)

    def clear(self) -> None:
        with self._lock:
            self._ip.clear()
            self._secondary.clear()


class RequestRateLimiter:
    """Single-key request counter (every attempt counts, not just failures)."""

    def __init__(self, *, max_requests: int, window_seconds: float) -> None:
        self._lock = Lock()
        self._store: dict[str, Deque[float]] = defaultdict(deque)
        self._max = max_requests
        self._window = window_seconds

    def check(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._store[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
