# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Hand-rolled p95 latency measurement for MCP recall (LUM-299)."""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable


def measure_recall_p95_ms(
    call_fn: Callable[[], None],
    *,
    iterations: int = 200,
    warmup: int = 20,
) -> float:
    """Return p95 latency in milliseconds for ``call_fn`` invocations."""
    for _ in range(warmup):
        call_fn()
    samples_ms: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        call_fn()
        samples_ms.append((time.perf_counter() - start) * 1000.0)
    return statistics.quantiles(samples_ms, n=100)[94]
