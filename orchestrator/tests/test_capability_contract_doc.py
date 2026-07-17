# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Guards for the Extension Contract v1 author guide (LUM-241).

Two cheap, hermetic guards:

1. ``test_capability_contract_doc_matches_models`` — the doc's field tables must
   mention every distinctive field of the contract models, so a future model
   rename surfaces as a doc-drift failure. Derived from the models across all
   four relevant classes (not a hand-copied list).
2. ``test_extending_docs_ship_publicly`` — the guide and the "fork this" mock
   reference must not be dropped from the public AGPL export. Uses a path-prefix
   containment check (not literal-string equality): the export does
   ``rm -rf "${OUT}/${line}"`` per strip-list line, so a coarse future line like
   ``docs/`` would delete ``docs/extending/`` even though the literal strings
   differ.
"""

from __future__ import annotations

from pathlib import Path

from models.capability import CapabilityAuth
from models.capability import CapabilityManifest
from models.capability import CapabilityTool
from models.capability_invoke import CapabilityInvokeMeta
from models.capability_invoke import CapabilityInvokeRequest

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "extending" / "capability-contract-v1.md"
STRIP_LIST = REPO / "scripts" / "public-export-strip-list.txt"

# Generic field names that are substring noise (appear everywhere) — excluded
# from the drift guard so it only protects distinctive contract fields.
_GENERIC = frozenset(
    {"name", "description", "version", "type", "transport", "user", "tool", "tools",
     "maintainer", "output", "error", "ok", "message", "code", "mode", "method", "path"}
)


def _distinctive_fields() -> set[str]:
    fields: set[str] = set()
    for model in (
        CapabilityManifest,
        CapabilityTool,
        CapabilityAuth,
        CapabilityInvokeRequest,
        CapabilityInvokeMeta,
    ):
        fields.update(model.model_fields)
    return {f for f in fields if f not in _GENERIC}


def test_capability_contract_doc_matches_models():
    assert DOC.exists(), f"missing author guide: {DOC}"
    text = DOC.read_text(encoding="utf-8")
    missing = sorted(f for f in _distinctive_fields() if f not in text)
    assert not missing, (
        "capability-contract-v1.md does not document these contract fields "
        f"(model↔doc drift): {missing}"
    )


def _strip_entries() -> set[str]:
    text = STRIP_LIST.read_text(encoding="utf-8")
    out: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.add(line.rstrip("/"))
    return out


def _is_dropped(protected: str, strip_entries: set[str]) -> bool:
    """True if any strip entry equals `protected` or a path-prefix ancestor of it."""
    p = protected.rstrip("/")
    parts = p.split("/")
    # Every ancestor directory prefix, plus the path itself.
    ancestors = {"/".join(parts[: i + 1]) for i in range(len(parts))}
    return bool(ancestors & strip_entries)


def test_extending_docs_ship_publicly():
    strip_entries = _strip_entries()
    for protected in ("docs/extending", "services/lumogis-mock-capability"):
        assert not _is_dropped(protected, strip_entries), (
            f"public-export strip list drops {protected!r} (or an ancestor of it) — "
            "the capability author guide and its 'fork this' reference must ship publicly"
        )
