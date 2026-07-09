# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Fixture loader for LUM-299 cursor integration harness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "coding_bank.json"

_CODING_ENTITY_TYPES = frozenset({
    "CODING_DECISION", "CODING_CONVENTION", "COMPONENT", "FAILURE",
    "SESSION", "TASK", "LIBRARY",
})


@dataclass(frozen=True)
class CodingBankFixture:
    raw: dict[str, Any]

    @property
    def version(self) -> int:
        return int(self.raw["version"])

    def memory_ids(self, bank: str) -> list[str]:
        return [m["memory_id"] for m in self.raw["banks"][bank]["memories"]]

    def memories(self, bank: str) -> list[dict[str, Any]]:
        return list(self.raw["banks"][bank]["memories"])

    def entities(self, bank: str) -> list[dict[str, Any]]:
        return list(self.raw["banks"][bank].get("entities") or [])

    def edges(self, bank: str) -> list[dict[str, Any]]:
        return list(self.raw["banks"][bank].get("edges") or [])

    def session_summaries(self) -> list[dict[str, Any]]:
        return list(self.raw.get("sessions") or [])

    def expected_recall_hits(self, query: str, bank: str) -> list[str]:
        q = query.strip().lower()
        for mapping in self.raw.get("recall_mappings") or []:
            if mapping["bank"] == bank and mapping["query"].strip().lower() == q:
                return list(mapping["memory_ids"])
        # Fallback: substring match on memory content
        hits: list[str] = []
        for mem in self.memories(bank):
            content = mem["content"].lower()
            if any(term in content for term in q.split() if len(term) > 2):
                hits.append(mem["memory_id"])
        return hits[:3]

    def coding_entity_types_present(self) -> set[str]:
        found: set[str] = set()
        for mem in self.memories("coding"):
            found.add(mem["entity_type"])
        return found


def load_coding_bank(path: Path | None = None) -> CodingBankFixture:
    fixture_path = path or _DEFAULT_FIXTURE
    if not fixture_path.is_file():
        raise FileNotFoundError(f"fixture not found: {fixture_path}")
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    version = data.get("version")
    if version != 1:
        raise ValueError(f"unsupported fixture version: {version!r}")
    if "banks" not in data or "coding" not in data["banks"]:
        raise ValueError("fixture missing banks.coding")
    for etype in _CODING_ENTITY_TYPES:
        if etype not in {m["entity_type"] for m in data["banks"]["coding"]["memories"]}:
            raise ValueError(f"fixture missing coding entity type: {etype}")
    return CodingBankFixture(raw=data)
