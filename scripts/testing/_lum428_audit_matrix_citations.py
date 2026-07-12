#!/usr/bin/env python3
"""LUM-428: validate TEST-COVERAGE-MATRIX ✅ rows (repo + plan + strict code rules)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))

from _lum384_plan_audit import (  # noqa: E402
    load_plan_index,
    plan_citations_for_feature,
    plan_index_summary,
    resolve_citation,
)
from _lum384_seed_matrices import (  # noqa: E402
    CURATED,
    Feature,
    index_tests,
    match_tests,
    route_audit_extras,
)

MATRIX_PATHS = [
    ROOT / "docs/testing/TEST-COVERAGE-MATRIX-core.md",
    ROOT / "docs/testing/TEST-COVERAGE-MATRIX-web.md",
    ROOT / "docs/private/testing/TEST-COVERAGE-MATRIX-kg.md",
    ROOT / "docs/private/testing/TEST-COVERAGE-MATRIX-hub.md",
]

ROW_RE = re.compile(
    r"^\| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|"
)
CITE_RE = re.compile(r"`([^`]+)` in `([^`]+)`")


def curated_by_feature() -> dict[str, Feature]:
    out: dict[str, Feature] = {}
    for m, s, f, n, layer, auto in CURATED:
        out[f] = Feature(m, s, f, n, layer, auto)
    for feat in route_audit_extras():
        out[feat.feature] = feat
    return out


def parse_green_rows(path: Path) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line.strip())
        if not m or m.group(5).strip() != "✅":
            continue
        id_, feature, src, _layer, _status, notes = (g.strip() for g in m.groups())
        rows.append((id_, feature, src, notes))
    return rows


def has_plan_corroboration(feat: Feature, rel: str, name: str, plan_records) -> bool:
    for cite in plan_citations_for_feature(
        feat.needles,
        plan_records,
        source_lum=feat.source_lum,
        plan_path=feat.plan_path,
        feature_title=feat.feature,
    ):
        resolved = resolve_citation(cite, [])
        if not resolved:
            if cite.rel_path.replace("\\", "/") == rel and (
                not cite.test_name or cite.test_name == name
            ):
                return True
            continue
        r, n = resolved
        if r == rel and (not cite.test_name or cite.test_name == name or n == name):
            return True
    return False


def main() -> int:
    plan_records = load_plan_index()
    summary = plan_index_summary(plan_records)
    by_name = curated_by_feature()
    test_index = index_tests()
    failures: list[str] = []
    warnings: list[str] = []
    plan_backed = 0
    code_only = 0

    for path in MATRIX_PATHS:
        for id_, feature, src, notes in parse_green_rows(path):
            cite = CITE_RE.search(notes)
            if not cite:
                failures.append(f"{path.name} {id_}: ✅ without `test` in `file` citation")
                continue
            test_name, test_file = cite.group(1), cite.group(2)
            full = ROOT / test_file
            if not full.is_file():
                failures.append(f"{path.name} {id_}: missing file `{test_file}`")
                continue
            text = full.read_text(encoding="utf-8", errors="replace")
            if test_name.startswith("test_") and not re.search(
                rf"def {re.escape(test_name)}\b", text
            ):
                failures.append(f"{path.name} {id_}: `{test_name}` not in `{test_file}`")

            feat = by_name.get(feature)
            if feat:
                expected_status, _expected_src, expected_note = match_tests(
                    feat, test_index, plan_records
                )
                if expected_status != "✅":
                    failures.append(
                        f"{path.name} {id_}: strict re-match → {expected_status} "
                        f"(was ✅ for `{test_name}`)"
                    )
                if "plan evidence" in notes:
                    plan_backed += 1
                elif has_plan_corroboration(feat, test_file, test_name, plan_records):
                    plan_backed += 1
                else:
                    code_only += 1
                    if feat.source_lum:
                        warnings.append(
                            f"{path.name} {id_}: plan row `{feat.source_lum}` "
                            f"but note lacks plan evidence tag"
                        )
            elif "plan evidence" in notes:
                plan_backed += 1
            else:
                code_only += 1

    if failures:
        print("LUM-428 audit: FAIL\n", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    total = sum(len(parse_green_rows(p)) for p in MATRIX_PATHS)
    print(
        f"LUM-428 audit: OK — {total} ✅ rows "
        f"({plan_backed} plan-backed or corroborated, {code_only} code-only)"
    )
    print(f"  plan_index: {summary}")
    if warnings:
        print("  warnings:")
        for w in warnings[:15]:
            print(f"    - {w}")
        if len(warnings) > 15:
            print(f"    … and {len(warnings) - 15} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
