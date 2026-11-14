# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared regex patterns for RELATES_TO MERGE policy checks (LUM-208).

Imported by ``scripts/check_graph_relates_to_merge_policy.py`` and projection
tests so policy and hermetic assertions stay in lockstep.
"""

from __future__ import annotations

import re

# Undirected relationship in a MERGE is invalid for FalkorDB (see Falkor MERGE docs).
UNDIRECTED_MERGE_RELATES_TO = re.compile(
    r"MERGE\s*\([^)]+\)\s*-\[[^\]]*RELATES_TO[^\]]*\]-\s*\(",
    re.IGNORECASE | re.DOTALL,
)

DIRECTED_MERGE_RELATES_TO = re.compile(
    r"MERGE\s*\([^)]+\)\s*-\[[^\]]*RELATES_TO[^\]]*\]->\s*\(",
    re.IGNORECASE | re.DOTALL,
)

# Canonical writes use lower_lumogis_id -> higher_lumogis_id, not an inbound edge on MERGE.
REVERSE_MERGE_RELATES_TO = re.compile(
    r"MERGE\s*\([^)]+\)\s*<-\[[^\]]*RELATES_TO",
    re.IGNORECASE | re.DOTALL,
)

__all__ = [
    "DIRECTED_MERGE_RELATES_TO",
    "REVERSE_MERGE_RELATES_TO",
    "UNDIRECTED_MERGE_RELATES_TO",
]
