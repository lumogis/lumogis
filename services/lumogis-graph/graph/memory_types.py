# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-124: canonical literals for entity memory_type (Postgres + FalkorDB).

Other modules (compaction, calibration) should import from here — do not fork
spellings (plan D8.1).
"""

from typing import Literal

MEMORY_TYPE_USER_PREFERENCE = "user_preference"
MEMORY_TYPE_CORRECTION = "correction"
MEMORY_TYPE_RELATIONSHIP = "relationship"

MemoryTypeLiteral = Literal["user_preference", "correction", "relationship"]

ALLOWED_MEMORY_TYPES: tuple[str, ...] = (
    MEMORY_TYPE_USER_PREFERENCE,
    MEMORY_TYPE_CORRECTION,
    MEMORY_TYPE_RELATIONSHIP,
)
