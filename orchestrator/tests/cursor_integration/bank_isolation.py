# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Bank isolation query generator for LUM-299."""

from __future__ import annotations

import random

from tests.cursor_integration.fixture_loader import CodingBankFixture


def random_isolation_queries(
    fixture: CodingBankFixture,
    *,
    n: int = 10,
    seed: int = 299,
) -> list[tuple[str, str, str]]:
    """Return ``(query, target_bank, forbidden_bank)`` tuples."""
    rng = random.Random(seed)
    _coding_ids = set(fixture.memory_ids("coding"))
    _personal_ids = set(fixture.memory_ids("personal"))
    pairs: list[tuple[str, str, str]] = []
    mappings = fixture.raw.get("recall_mappings") or []
    coding_maps = [m for m in mappings if m["bank"] == "coding"]
    personal_maps = [m for m in mappings if m["bank"] == "personal"]
    pool = coding_maps + personal_maps
    if not pool:
        for mem in fixture.memories("coding")[:n]:
            words = [w for w in mem["content"].split() if len(w) > 4][:3]
            pairs.append((" ".join(words), "coding", "personal"))
        return pairs[:n]
    while len(pairs) < n:
        m = rng.choice(pool)
        target = m["bank"]
        forbidden = "personal" if target == "coding" else "coding"
        pairs.append((m["query"], target, forbidden))
    return pairs[:n]
