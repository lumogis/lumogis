# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Data contracts + identity helpers for code-structure ingest (LUM-301).

Pure, deterministic, no I/O. The identity helpers (``lumogis_id``,
``module_prefix``, ``edge_evidence_id``) are load-bearing: two implementations
must derive the *same* ids for the same symbol so re-index MERGEs in place
instead of duplicating (see plan §"Identity & derivation").
"""

from __future__ import annotations

import hashlib
import posixpath
import unicodedata
from dataclasses import dataclass
from dataclasses import field

from pydantic import BaseModel
from pydantic import Field

# Graph property marking provenance of structure-ingested rows. Graph-internal
# only — NOT a Postgres evidence_type and NOT an MCP add_memory evidence kind.
EVIDENCE_TYPE_CODE_STRUCTURE = "CODE_STRUCTURE"

# v1 language scope.
SUPPORTED_LANGS = ("python", "typescript")


# --------------------------------------------------------------------------- #
# Parse-stage contracts (language-agnostic; produced by parser.py)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Symbol:
    name: str
    qualname: str  # module-qualified, e.g. "pkg.mod.Class.method"
    kind: str  # "function" | "class" | "method"
    file: str  # root-relative POSIX path
    start_line: int
    end_line: int
    signature: str | None
    lang: str
    bases: tuple[str, ...] = ()  # directly named base classes / extends targets


@dataclass(frozen=True)
class CallSite:
    caller_qualname: str
    callee_name: str
    file: str
    line: int


@dataclass(frozen=True)
class ImportRef:
    module: str
    is_external: bool
    file: str


@dataclass(frozen=True)
class ParsedModule:
    file: str
    lang: str
    symbols: tuple[Symbol, ...]
    calls: tuple[CallSite, ...]
    imports: tuple[ImportRef, ...]


# --------------------------------------------------------------------------- #
# Projection contracts (produced by mapper.py, consumed by writer.py)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ComponentNode:
    lumogis_id: str
    label: str  # "Component" | "Library"
    props: dict
    user_id: str


@dataclass(frozen=True)
class StructEdge:
    from_id: str
    to_id: str
    rel_type: str  # "CALLS" | "DEPENDS_ON" | "IMPLEMENTS"
    evidence_id: str
    props: dict


@dataclass
class IngestReport:
    roots: list[str]
    files_parsed: int = 0
    components: int = 0
    edges: int = 0
    skipped: list[str] = field(default_factory=list)
    unresolved_calls: int = 0
    oversize_skipped: list[str] = field(default_factory=list)
    ingest_run_id: str = ""
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "roots": self.roots,
            "files_parsed": self.files_parsed,
            "components": self.components,
            "edges": self.edges,
            "skipped": self.skipped,
            "unresolved_calls": self.unresolved_calls,
            "oversize_skipped": self.oversize_skipped,
            "ingest_run_id": self.ingest_run_id,
            "duration_ms": self.duration_ms,
        }


class ReindexRequest(BaseModel):
    """Body for POST /codegraph/reindex (all optional → fall back to env)."""

    roots: list[str] | None = Field(default=None)
    bank: str | None = Field(default=None)


# --------------------------------------------------------------------------- #
# Identity & derivation (plan §"Identity & derivation" — arbitration R1)
# --------------------------------------------------------------------------- #
_PY_INIT_SUFFIX = ".__init__"


def module_prefix(file_rel_posix: str, lang: str) -> str:
    """Filesystem path (root-relative, POSIX) -> dotted module prefix.

    tree-sitter gives in-file nesting but not the package path; derive it here.
    e.g. "orchestrator/services/tools.py" -> "orchestrator.services.tools".
    """
    path = file_rel_posix.strip("/")
    base, _dot, _ext = path.rpartition(".")
    stem = base if _dot else path  # drop a single extension if present
    dotted = stem.replace("/", ".")
    if lang == "python" and dotted.endswith(_PY_INIT_SUFFIX):
        dotted = dotted[: -len(_PY_INIT_SUFFIX)]
    # P3-4: a dotfile (e.g. ".eslintrc.ts") would yield a leading-dot segment;
    # a module qualname never starts/ends with a dot or has empty segments.
    return ".".join(seg for seg in dotted.split(".") if seg)


def make_qualname(file_rel_posix: str, lang: str, nesting: list[str]) -> str:
    """module_prefix + in-file nesting chain (class/function names)."""
    prefix = module_prefix(file_rel_posix, lang)
    parts = [p for p in nesting if p]
    return ".".join([prefix, *parts]) if parts else prefix


def lumogis_id(bank: str, qualname: str, kind: str) -> str:
    """Stable node id = sha1(bank|qualname|kind.lower()).

    Implementation refinement vs the plan's ``sha1(bank|file|qualname|kind)``:
    ``qualname`` already begins with ``module_prefix`` (the dotted root-relative
    file path), so ``file`` is redundant for uniqueness. Dropping it lets an
    internal import's ``DEPENDS_ON`` *target* id be computed from the import
    module string alone (kind="module"), so cross-file edge endpoints line up on
    MERGE. No lowercasing of qualname (identifiers are case-sensitive); NFC.
    """
    canonical = "|".join(
        [
            unicodedata.normalize("NFC", bank),
            unicodedata.normalize("NFC", qualname),
            kind.lower(),
        ]
    )
    return hashlib.sha1(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()


def edge_evidence_id(from_id: str, to_id: str, rel_type: str) -> str:
    """Deterministic edge key so create_edge MERGE-on-(from,to,rel,evidence_id)
    is idempotent across re-runs."""
    return hashlib.sha1(
        f"{from_id}|{to_id}|{rel_type}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()


def to_posix_rel(path: str, root: str) -> str:
    """Return ``path`` relative to ``root`` with forward slashes."""
    rel = posixpath.relpath(path.replace("\\", "/"), root.replace("\\", "/"))
    return rel
