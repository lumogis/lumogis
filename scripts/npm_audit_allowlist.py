#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Fail-closed allowlist for ``npm audit --json`` (called by scripts/audit_local.sh).

Usage::

    python3 scripts/npm_audit_allowlist.py <web_dir> <audit_json> [--today YYYY-MM-DD]

Reads the audit report and ``<web_dir>/package-lock.json`` and suppresses an advisory only when
EVERY condition of its ``ALLOWLIST`` entry holds (see ``entry_applies``). Anything left over
makes the script exit 1. All output goes to stderr; stdout is unused.

Every entry must carry a ``review_by`` date: past it, the gate fails until someone re-checks
upstream and edits the date. New entries go in ``ALLOWLIST`` — do not add gating code elsewhere.

``--today`` exists for tests only; audit_local.sh never passes it.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT_NAME = "<root>"
_SRC_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


@dataclass(frozen=True)
class AllowEntry:
    ghsa: str
    package: str
    max_version: tuple[int, int, int]
    # Packages allowed to be popped as effects-only parents of ``package``.
    chain: frozenset[str]
    # Lockfile packages allowed to depend on ``package``.
    allowed_dependents: frozenset[str]
    require_dev: bool
    forbid_src_import: bool
    review_by: datetime.date
    rationale: str
    removal_condition: str


ALLOWLIST: tuple[AllowEntry, ...] = (
    AllowEntry(
        ghsa="GHSA-gcqx-w48m-w2h4",
        package="deepmerge",
        max_version=(4, 3, 1),
        chain=frozenset({"@rollup/plugin-node-resolve", "workbox-build", "vite-plugin-pwa"}),
        allowed_dependents=frozenset({"@rollup/plugin-node-resolve"}),
        require_dev=True,
        forbid_src_import=True,
        review_by=datetime.date(2027, 2, 4),
        rationale=(
            "no fixed release exists; build-time merge of package `exports` metadata via "
            "workbox-build's rollup bundling of the SW; never imported from `src/`"
        ),
        removal_condition=(
            "a deepmerge > 4.3.1 is published, or @rollup/plugin-node-resolve drops deepmerge"
        ),
    ),
)


def parse_ver(v: str | None) -> tuple[int, int, int] | None:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def via_only(entry_json: dict, ghsa: str, chain: frozenset[str] | set[str]) -> bool:
    """True if every ``via`` item is an advisory dict for ``ghsa`` or a string in ``chain``."""
    via = entry_json.get("via") or []
    if not via:
        return False
    for item in via:
        if isinstance(item, str):
            if item not in chain:
                return False
        elif isinstance(item, dict):
            if ghsa not in (item.get("url") or ""):
                return False
        else:
            return False
    return True


def _pkg_name(path: str) -> str:
    if path == "":
        return ROOT_NAME
    return path.rsplit("node_modules/", 1)[-1]


def locked_instances(lock: dict, package: str) -> list[tuple[str, dict]]:
    suffix = "/node_modules/" + package
    top = "node_modules/" + package
    return [
        (path, meta)
        for path, meta in (lock.get("packages") or {}).items()
        if path == top or path.endswith(suffix)
    ]


def dependents_of(lock: dict, package: str) -> set[str]:
    out: set[str] = set()
    for path, meta in (lock.get("packages") or {}).items():
        for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            if package in (meta.get(field) or {}):
                out.add(_pkg_name(path))
                break
    return out


def src_imports(src_root: Path, package: str) -> bool:
    """True if any file under ``src_root`` imports ``package`` (or a subpath of it).

    A missing ``src_root`` raises, so the caller fails closed instead of passing vacuously.
    """
    if not src_root.is_dir():
        raise FileNotFoundError(f"src root not found: {src_root}")
    spec = re.compile(
        r"""\b(?:from|import|require)\s*\(?\s*["']""" + re.escape(package) + r"""(?:/[^"']*)?["']"""
    )
    for path in src_root.rglob("*"):
        if path.suffix not in _SRC_SUFFIXES or not path.is_file():
            continue
        if spec.search(path.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def entry_applies(
    entry: AllowEntry,
    vulns: dict,
    lock: dict,
    src_root: Path,
    today: datetime.date,
) -> tuple[bool, str]:
    """All-of check. Returns (applies, reason); ``reason`` explains the first failed condition."""
    pkg = entry.package

    # 1. The package's own advisories are exactly this GHSA (no string via, no other advisory).
    own = vulns.get(pkg)
    if own is None or not via_only(own, entry.ghsa, frozenset()):
        return False, f"{pkg} is reported for something other than {entry.ghsa}"

    # 2. Locked, and every locked instance is at or below max_version.
    instances = locked_instances(lock, pkg)
    if not instances:
        return False, f"{pkg} not found in package-lock.json"
    for path, meta in instances:
        ver = parse_ver(meta.get("version"))
        if ver is None or ver > entry.max_version:
            return False, (
                f"{path} is {meta.get('version')!r}, above the allowlisted maximum "
                f"{'.'.join(map(str, entry.max_version))}"
            )

    # 3. require_dev: every instance is dev-only (devOptional does not count).
    if entry.require_dev:
        for path, meta in instances:
            if meta.get("dev") is not True:
                why = " (devOptional is not enough)" if meta.get("devOptional") else ""
                return False, f"{path} is not dev-only; require_dev is set{why}"

    # 4. Only allowed packages depend on it.
    extra = dependents_of(lock, pkg) - entry.allowed_dependents
    if extra:
        return False, f"unexpected dependents of {pkg}: {', '.join(sorted(extra))}"

    # 5. Not expired.
    if today > entry.review_by:
        return False, f"review_by {entry.review_by.isoformat()} passed — re-check upstream and update the entry"

    # 7. (6 is applied by the caller when popping.) Never imported from src/.
    if entry.forbid_src_import and src_imports(src_root, pkg):
        return False, f"{pkg} is imported from src/ (would ship in the bundle)"

    return True, ""


def _pop_chain(entry: AllowEntry, vulns: dict) -> None:
    """Condition 6: pop chain parents transitively, effects-only, named in ``entry.chain`` only."""
    popped = {entry.package}
    vulns.pop(entry.package, None)
    changed = True
    while changed:
        changed = False
        for name in sorted(entry.chain):
            item = vulns.get(name)
            if item is None or name in popped:
                continue
            via = item.get("via") or []
            if via and all(isinstance(v, str) and v in popped for v in via):
                popped.add(name)
                vulns.pop(name, None)
                changed = True


def main(argv: list[str]) -> int:
    args = list(argv)
    today = datetime.date.today()
    if "--today" in args:
        i = args.index("--today")
        raw = args[i + 1] if i + 1 < len(args) else ""
        try:
            today = datetime.date.fromisoformat(raw)
        except ValueError:
            print(f"audit_local: invalid --today value {raw} (expected YYYY-MM-DD)", file=sys.stderr)
            return 2
        del args[i : i + 2]
        print("audit_local: --today override in effect (tests only)", file=sys.stderr)
    if len(args) != 2:
        print(
            "usage: npm_audit_allowlist.py <web_dir> <audit_json> [--today YYYY-MM-DD]",
            file=sys.stderr,
        )
        return 2

    web_dir = Path(args[0])
    report = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    if "vulnerabilities" not in report:
        print("audit_local: npm audit report has no 'vulnerabilities' key (fail closed)", file=sys.stderr)
        return 1
    vulns = dict(report["vulnerabilities"] or {})
    if not vulns:
        return 0

    lock = json.loads((web_dir / "package-lock.json").read_text(encoding="utf-8"))
    src_root = web_dir / "src"

    for entry in ALLOWLIST:
        if entry.package not in vulns:
            continue
        ok, reason = entry_applies(entry, vulns, lock, src_root, today)
        if not ok:
            print(
                f"audit_local: {entry.ghsa} on {entry.package} — NOT applied: {reason}",
                file=sys.stderr,
            )
            continue
        versions = sorted({m.get("version", "?") for _, m in locked_instances(lock, entry.package)})
        print(
            f"audit_local: allowlisted {entry.ghsa} on {entry.package}@{','.join(versions)} "
            f"(review by {entry.review_by.isoformat()}): {entry.rationale}",
            file=sys.stderr,
        )
        _pop_chain(entry, vulns)

    if vulns:
        print(
            "audit_local: npm audit still reports vulnerabilities after allowlist: "
            + ", ".join(sorted(vulns)),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
