# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Map a ``ParsedModule`` → ``Component``/``Library`` nodes + structural edges.

Pure and deterministic; no I/O, no time, no user. Run-level properties
(``user_id``, ``ingest_run_id``, ``timestamp``, ``evidence_type``) are stamped
later by ``writer.project_code_structure`` so this stays a pure function.

v1 resolution scope (plan §mapper, arbitration R1):
* **CALLS** — only when ``callee_name`` resolves to *exactly one* symbol defined
  in the same module; 0 or >1 matches are dropped and counted (unresolved).
* **IMPLEMENTS** — only directly-named base classes that resolve to a same-module
  class; cross-file/transitive deferred.
* **DEPENDS_ON** — external imports → ``Library`` node + edge; first-party
  absolute imports → edge to the target module node (by qualname); relative
  imports deferred (ambiguous target).
"""

from __future__ import annotations

from dataclasses import dataclass

from codegraph.models import ComponentNode
from codegraph.models import ParsedModule
from codegraph.models import StructEdge
from codegraph.models import edge_evidence_id
from codegraph.models import lumogis_id


@dataclass
class MapResult:
    components: list[ComponentNode]
    edges: list[StructEdge]
    unresolved_calls: int = 0


def _component(bank: str, *, qualname: str, kind: str, props: dict) -> ComponentNode:
    return ComponentNode(
        lumogis_id=lumogis_id(bank, qualname, kind),
        label="Component",
        props=props,
        user_id="",  # stamped by the writer at projection time
    )


def _edge(from_id: str, to_id: str, rel_type: str) -> StructEdge:
    return StructEdge(
        from_id=from_id,
        to_id=to_id,
        rel_type=rel_type,
        evidence_id=edge_evidence_id(from_id, to_id, rel_type),
        props={},  # run-level props stamped by the writer
    )


def map_module(parsed: ParsedModule, *, bank: str) -> MapResult:
    components: list[ComponentNode] = []
    edges: list[StructEdge] = []
    unresolved = 0

    # qualname -> kind (for computing endpoint ids); name -> [qualnames] (resolution)
    qual_kind: dict[str, str] = {}
    by_name: dict[str, list[tuple[str, str]]] = {}  # name -> [(qualname, kind)]
    module_qual = parsed.file  # fallback; overwritten by the module symbol below

    for sym in parsed.symbols:
        qual_kind[sym.qualname] = sym.kind
        if sym.kind == "module":
            module_qual = sym.qualname
        else:
            by_name.setdefault(sym.name, []).append((sym.qualname, sym.kind))
        components.append(
            _component(
                bank,
                qualname=sym.qualname,
                kind=sym.kind,
                props={
                    "name": sym.name,
                    "qualname": sym.qualname,
                    "file": sym.file,
                    "lang": sym.lang,
                    "kind": sym.kind,
                    "signature": sym.signature,
                    "start_line": sym.start_line,
                    "end_line": sym.end_line,
                },
            )
        )

    module_id = lumogis_id(bank, module_qual, "module")

    def id_for_qual(qualname: str) -> str:
        return lumogis_id(bank, qualname, qual_kind.get(qualname, "module"))

    # CALLS — intra-module, unique-name resolution only.
    for call in parsed.calls:
        matches = by_name.get(call.callee_name, [])
        if len(matches) != 1:
            unresolved += 1
            continue
        callee_qual, callee_kind = matches[0]
        from_id = id_for_qual(call.caller_qualname)
        to_id = lumogis_id(bank, callee_qual, callee_kind)
        if from_id == to_id:
            continue  # ignore trivial self-recursion edges
        edges.append(_edge(from_id, to_id, "CALLS"))

    # IMPLEMENTS — directly-named base classes resolving to a same-module class.
    for sym in parsed.symbols:
        if sym.kind != "class" or not sym.bases:
            continue
        cls_id = lumogis_id(bank, sym.qualname, "class")
        for base in sym.bases:
            cands = [q for (q, k) in by_name.get(base, []) if k == "class"]
            if len(cands) == 1:
                edges.append(_edge(cls_id, lumogis_id(bank, cands[0], "class"), "IMPLEMENTS"))

    # DEPENDS_ON + Library nodes from imports.
    seen_lib: set[str] = set()
    for imp in parsed.imports:
        if not imp.module:
            continue
        if imp.is_external:
            lib_id = lumogis_id(bank, imp.module, "library")
            if lib_id not in seen_lib:
                seen_lib.add(lib_id)
                components.append(
                    ComponentNode(
                        lumogis_id=lib_id,
                        label="Library",
                        props={"name": imp.module, "qualname": imp.module, "kind": "library"},
                        user_id="",
                    )
                )
            edges.append(_edge(module_id, lib_id, "DEPENDS_ON"))
        # First-party (internal) imports are DEFERRED to the cross-file/SCIP phase
        # (v1 scope). The raw import string is not a resolvable module qualname for
        # relative imports (`./util`, `from .sibling`), and absolute first-party
        # resolution needs the global module->file map. Emitting an edge from the
        # raw string produced endpoints that never matched the real node (silent
        # zero-row MERGE), so external imports -> Library are the only v1 import edges.

    return MapResult(components=components, edges=edges, unresolved_calls=unresolved)
