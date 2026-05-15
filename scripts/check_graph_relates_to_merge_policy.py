#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-208: forbid undirected or reverse RELATES_TO MERGE patterns in Python sources.

Scans lumogis-graph and orchestrator service/plugin trees for Cypher-shaped string
literals. Uses AST walking so implicit ``"a" "b"`` folding, ``+``-joined fragments,
and ``f``-string literal runs are merged before regex checks.

Loads ``relates_to_merge_patterns`` via ``importlib`` so this script runs under a
plain Python without importing ``graph`` package side effects (``graph/__init__.py``
pulls FastAPI).

Limitation: ``JoinedStr`` that interpolates all MERGE text via ``FormattedValue``
nodes only (no adjacent literal chunks containing ``MERGE``) can evade static
detection — ``m1_compat`` live FalkorDB tests are the backstop; extend this
checker under LUM-52 / LUM-208 if that class appears in production code.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_merge_patterns():
    path = (
        _repo_root()
        / "services"
        / "lumogis-graph"
        / "graph"
        / "relates_to_merge_patterns.py"
    )
    spec = importlib.util.spec_from_file_location(
        "lumogis.relates_to_merge_patterns",
        path,
    )
    if spec is None or spec.loader is None:
        print("check_graph_relates_to_merge_policy: cannot load patterns", file=sys.stderr)
        sys.exit(2)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mp = _load_merge_patterns()
REVERSE_MERGE_RELATES_TO = _mp.REVERSE_MERGE_RELATES_TO
UNDIRECTED_MERGE_RELATES_TO = _mp.UNDIRECTED_MERGE_RELATES_TO

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_TOOL = 2


def try_concat_str_add(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = try_concat_str_add(node.left)
        right = try_concat_str_add(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def iter_joinedstr_literal_runs(node: ast.JoinedStr) -> list[str]:
    runs: list[str] = []
    buf: list[str] = []
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            buf.append(part.value)
        else:
            if buf:
                runs.append("".join(buf))
                buf = []
    if buf:
        runs.append("".join(buf))
    return runs


def iter_fragments_for_module(mod: ast.AST) -> list[str]:
    out: list[str] = []

    for node in ast.walk(mod):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.extend(iter_joinedstr_literal_runs(node))

    for node in ast.walk(mod):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            cat = try_concat_str_add(node)
            if cat is not None:
                out.append(cat)

    return out


def check_source(text: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        mod = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]

    for frag in iter_fragments_for_module(mod):
        if UNDIRECTED_MERGE_RELATES_TO.search(frag):
            errors.append(
                f"{path}: undirected RELATES_TO MERGE detected "
                "(FalkorDB rejects this pattern; use directed MERGE lower->higher)"
            )
        if REVERSE_MERGE_RELATES_TO.search(frag):
            errors.append(
                f"{path}: reverse/inbound RELATES_TO MERGE detected "
                "(use MERGE (lower)-[r:RELATES_TO]->(higher))"
            )
    return errors


def iter_scan_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            files.append(p)
    return sorted(files)


def run_scan() -> list[str]:
    root = _repo_root()
    paths = iter_scan_files(
        [
            root / "services" / "lumogis-graph",
            root / "orchestrator" / "services",
            root / "orchestrator" / "plugins",
        ]
    )
    errors: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if "RELATES_TO" not in text or "MERGE" not in text:
            continue
        errors.extend(check_source(text, path))
    return errors


def _self_check() -> int:
    """Temp fixtures (a)–(e); expect violations (a)–(c), pass (d)–(e)."""
    undirected_hist = (
        "MATCH (proj:Entity) "
        "MATCH (other_proj:Entity) "
        "MERGE (proj)-[r2:RELATES_TO "
        "{scope: $target_scope}]-(other_proj) "
    )
    reverse_merge = (
        "MATCH (a:Entity) MATCH (b:Entity) "
        "MERGE (b)<-[r2:RELATES_TO]->(a) "
    )
    directed_ok = (
        "MATCH (a:Entity) MATCH (b:Entity) "
        "MERGE (a)-[r2:RELATES_TO {scope: $s}]->(b) "
    )
    undirected_match_only = (
        "MATCH (proj:Entity)-[r2:RELATES_TO]-(other_proj) RETURN proj "
    )
    splits = (
        "MERGE (proj)-[r2:RELATES_TO {sc"
        + "ope: $target_scope}]-(other_proj)"
    )

    cases_bad = {
        "a_undirected": undirected_hist,
        "b_splits": splits,
        "c_reverse": reverse_merge,
    }
    cases_ok = {
        "d_directed": directed_ok,
        "e_match": undirected_match_only,
    }

    for name, src in cases_bad.items():
        errs = check_source(f"x = {src!r}\n", Path(f"<selfcheck-bad-{name}>"))
        if not errs:
            print(f"self-check: expected violation for {name}", file=sys.stderr)
            return EXIT_VIOLATION

    for name, src in cases_ok.items():
        errs = check_source(f"x = {src!r}\n", Path(f"<selfcheck-ok-{name}>"))
        if errs:
            for e in errs:
                print(e, file=sys.stderr)
            print(f"self-check: false positive on {name}", file=sys.stderr)
            return EXIT_VIOLATION

    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="run built-in regression fixtures and exit",
    )
    args = parser.parse_args()
    if args.self_check:
        return _self_check()

    errors = run_scan()
    if errors:
        for msg in errors:
            print(msg, file=sys.stderr)
        return EXIT_VIOLATION
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
