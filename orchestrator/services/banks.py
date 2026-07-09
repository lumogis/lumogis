# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Canonical MCP memory bank registry and isolation helpers (LUM-293)."""

from __future__ import annotations

KNOWN_BANKS: frozenset[str] = frozenset({"coding", "personal", "default"})
MCP_MEMORY_SOURCE = "mcp"


def is_cross_bank(bank: str) -> bool:
    return bank == "*"


def validate_bank_for_write(bank: str) -> str:
    bank = bank.strip()
    if not bank:
        raise ValueError("bank must be non-empty")
    if bank not in KNOWN_BANKS:
        raise ValueError(f"bank {bank!r} not in {sorted(KNOWN_BANKS)}")
    return bank


def validate_bank_for_recall(bank: str) -> str:
    bank = bank.strip()
    if is_cross_bank(bank):
        return bank
    return validate_bank_for_write(bank)


def falkordb_graph_name(bank: str) -> str:
    """Return the FalkorDB graph key for a concrete bank (never ``*``)."""
    return validate_bank_for_write(bank)


def qdrant_bank_filter(bank: str) -> list[dict] | None:
    """Return the bank ``must`` clause for Qdrant, or ``None`` when cross-bank."""
    if is_cross_bank(bank):
        return None
    validated = validate_bank_for_write(bank)
    return [{"key": "bank", "match": {"value": validated}}]
