#!/usr/bin/env python3
"""LUM-384/428: extract features and test evidence from active + archived Cursor plans."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANS_ACTIVE = ROOT / ".cursor/plans"
PLANS_ARCHIVED = ROOT / ".cursor/plans/archived"

# Repo-relative test paths only (no .cursor, no node_modules).
TEST_PATH_RE = re.compile(
    r"(?:^|[\s`\"'(])"
    r"((?:orchestrator|stack-control|scripts|clients|services|tests)/"
    r"[\w./-]*(?:test_[\w.-]+\.py|test_main\.py|[\w.-]+\.(?:test|spec)\.(?:ts|tsx)))",
    re.I,
)
CITE_RE = re.compile(
    r"`(test_[a-zA-Z0-9_]+)`\s+in\s+`((?:orchestrator|stack-control|scripts|clients|services)/[^`]+)`"
)
BACKTICK_PATH_RE = re.compile(
    r"`((?:orchestrator|stack-control|scripts|clients|services)/[^`\s]+\.(?:py|ts|tsx))`"
)
TEST_NAME_RE = re.compile(r"\b(test_[a-zA-Z0-9_]+)\b")
API_PATH_RE = re.compile(r"/api/v1/[a-z0-9_/-]+", re.I)
LUM_ID_RE = re.compile(r"linear_issue_id:\s*(LUM-\d+)", re.I)
LUM_INLINE_RE = re.compile(r"\bLUM-\d+\b")


@dataclass
class PlanCitation:
    test_name: str | None  # test_* or Vitest stem
    rel_path: str
    source: str  # plan section hint


@dataclass
class PlanRecord:
    path: str  # repo-relative from ROOT via .cursor symlink
    lum_id: str | None
    title: str
    verified: bool  # implemented / verify-plan complete
    active: bool  # under .cursor/plans/ root (not archived/)
    citations: list[PlanCitation] = field(default_factory=list)
    api_paths: list[str] = field(default_factory=list)
    product_paths: list[str] = field(default_factory=list)


def _is_verified_plan(text: str) -> bool:
    if re.search(r"^status:\s*implemented\b", text, re.M | re.I):
        return True
    if "✅ Complete" in text or "⚠️ Complete with issues" in text:
        return True
    if re.search(r"^test_result:\s*passing\b", text, re.M | re.I):
        return True
    return False


def _plan_title(text: str, fallback: str) -> str:
    m = re.search(r"^name:\s*(.+)$", text, re.M)
    if m:
        return m.group(1).strip()
    m = re.search(r"^#\s+Plan:\s*(.+)$", text, re.M)
    if m:
        return m.group(1).strip()
    return fallback


def _normalise_test_path(raw: str) -> str | None:
    p = raw.strip().strip(",").strip("'\"")
    if not p or "node_modules" in p or ".cursor/" in p:
        return None
    if p.startswith("./"):
        p = p[2:]
    if not re.match(r"^(orchestrator|stack-control|scripts|clients|services)/", p, re.I):
        return None
    return p.replace("\\", "/")


def _extract_citations(text: str) -> list[PlanCitation]:
    found: list[PlanCitation] = []
    seen: set[tuple[str | None, str]] = set()

    def add(name: str | None, path: str, source: str) -> None:
        np = _normalise_test_path(path)
        if not np:
            return
        key = (name, np)
        if key in seen:
            return
        seen.add(key)
        found.append(PlanCitation(test_name=name, rel_path=np, source=source))

    for m in CITE_RE.finditer(text):
        add(m.group(1), m.group(2), "plan cite")
    for m in TEST_PATH_RE.finditer(text):
        add(None, m.group(1), "plan path")
    for m in BACKTICK_PATH_RE.finditer(text):
        path = m.group(1)
        if "test_" in path or ".test." in path or ".spec." in path:
            add(None, path, "plan path")
    # Implementation log / test cases blocks — pair nearby test names with paths
    for block in re.findall(
        r"(?:## Test cases|### Test|Implementation [Ll]og|Tests run)(.{0,8000}?)(?:\n## |\Z)",
        text,
        re.S,
    ):
        paths = [_normalise_test_path(x) for x in TEST_PATH_RE.findall(block)]
        paths = [p for p in paths if p]
        names = TEST_NAME_RE.findall(block)
        for p in paths:
            name = None
            for n in names:
                if n in block and p in block:
                    name = n
                    break
            add(name, p, "plan tests section")
    return found


def _extract_product_paths(text: str) -> list[str]:
    paths: list[str] = []
    for section in ("New files", "Modified files"):
        m = re.search(rf"## {section}\s*\n(.*?)(?:\n## |\Z)", text, re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            for p in BACKTICK_PATH_RE.findall(line):
                np = _normalise_test_path(p) or p.replace("\\", "/")
                if np.startswith(
                    ("orchestrator/", "clients/", "services/", "stack-control/", "scripts/")
                ):
                    paths.append(np)
    return paths


def parse_plan_file(plan_path: Path) -> PlanRecord | None:
    try:
        text = plan_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lum_m = LUM_ID_RE.search(text)
    lum_id = lum_m.group(1) if lum_m else None
    if not lum_id and not plan_path.name.startswith("LUM-"):
        # Legacy slug plan — still index tests, no LUM row promotion
        lum_id = None
    rel_plan = plan_path.relative_to(ROOT).as_posix()
    active = plan_path.parent == PLANS_ACTIVE
    return PlanRecord(
        path=rel_plan,
        lum_id=lum_id,
        title=_plan_title(text, plan_path.stem),
        verified=_is_verified_plan(text),
        active=active,
        citations=_extract_citations(text),
        api_paths=sorted(set(API_PATH_RE.findall(text))),
        product_paths=_extract_product_paths(text),
    )


def iter_plan_files() -> list[Path]:
    out: list[Path] = []
    if PLANS_ACTIVE.is_dir():
        out.extend(sorted(PLANS_ACTIVE.glob("*.plan.md")))
    if PLANS_ARCHIVED.is_dir():
        out.extend(sorted(PLANS_ARCHIVED.glob("*.plan.md")))
    return out


def load_plan_index() -> list[PlanRecord]:
    records: list[PlanRecord] = []
    for p in iter_plan_files():
        rec = parse_plan_file(p)
        if rec:
            records.append(rec)
    return records


def guess_matrix(rec: PlanRecord) -> str:
    blob = " ".join(rec.product_paths + [rec.path] + rec.api_paths).lower()
    title = rec.title.lower()
    if "lumogis-desktop" in blob or "desktop" in title:
        return "desktop"
    if "lumogis-graph" in blob or re.search(r"\bkg\b|graph", title):
        return "kg"
    if "lumogis-web" in blob or "web" in title:
        return "web"
    return "core"


def guess_section(matrix: str, rec: PlanRecord) -> str:
    defaults = {"core": "1.9", "web": "2.4", "kg": "3.2", "desktop": "4.2"}
    return defaults.get(matrix, "1.9")


def plan_needles(rec: PlanRecord) -> list[str]:
    needles: list[str] = []
    if rec.lum_id:
        needles.append(rec.lum_id)
    for p in rec.api_paths[:6]:
        needles.append(p)
    for c in rec.citations[:8]:
        if c.test_name:
            needles.append(c.test_name)
        needles.append(Path(c.rel_path).stem)
        needles.append(c.rel_path)
    for p in rec.product_paths[:6]:
        needles.append(Path(p).name)
    return needles


def supplemental_features(
    records: list[PlanRecord], existing_titles: set[str]
) -> list[tuple[str, str, str, list[str], str, bool, str, str]]:
    """Extra rows from verified plans not obviously covered by curated titles."""
    out: list[tuple[str, str, str, list[str], str, bool, str, str]] = []
    for rec in records:
        if not rec.verified or not rec.lum_id:
            continue
        label = f"{rec.lum_id}: {rec.title[:72]}"
        if label.lower() in existing_titles:
            continue
        # Skip if any citation needle already maps to an existing feature title word overlap
        title_words = set(re.findall(r"[a-z]{4,}", rec.title.lower()))
        overlap = False
        for t in existing_titles:
            if len(title_words & set(re.findall(r"[a-z]{4,}", t))) >= 2:
                overlap = True
                break
        if overlap:
            continue
        if not rec.citations or not any(c.test_name for c in rec.citations):
            continue
        if len(out) >= 12:
            break
        matrix = guess_matrix(rec)
        section = guess_section(matrix, rec)
        layer = "unit"
        if any(".spec." in c.rel_path or ".test.ts" in c.rel_path for c in rec.citations):
            layer = (
                "e2e"
                if any("e2e" in c.rel_path for c in rec.citations)
                else "web"
            )
        out.append(
            (matrix, section, label, plan_needles(rec), layer, True, rec.lum_id, rec.path)
        )
        existing_titles.add(label.lower())
    return out


def lums_for_needles(needles: list[str], records: list[PlanRecord]) -> list[str]:
    found: list[str] = []
    blob = " ".join(n.lower() for n in needles)
    for rec in records:
        if not rec.lum_id:
            continue
        hit = False
        if rec.lum_id.lower() in blob:
            hit = True
        for n in needles:
            nl = n.lower()
            if nl and nl in " ".join(rec.api_paths).lower():
                hit = True
            for c in rec.citations:
                if nl in c.rel_path.lower() or (c.test_name and nl in c.test_name.lower()):
                    hit = True
        if hit and rec.lum_id not in found:
            found.append(rec.lum_id)
    return found[:3]


def plan_records_for_feature(
    feat_needles: list[str],
    records: list[PlanRecord],
    *,
    source_lum: str | None = None,
    plan_path: str | None = None,
    feature_title: str = "",
) -> list[PlanRecord]:
    """Scope plan evidence to one plan file or parent LUM (supplemental rows)."""
    if plan_path:
        scoped = [r for r in records if r.path == plan_path]
        if scoped:
            return scoped
    if source_lum:
        scoped = [r for r in records if r.lum_id == source_lum]
        if scoped:
            return scoped
    title_lums = LUM_INLINE_RE.findall(feature_title)
    if title_lums:
        scoped = [r for r in records if r.lum_id in title_lums]
        if scoped:
            return scoped
    return records


def plan_citations_for_feature(
    feat_needles: list[str],
    records: list[PlanRecord],
    *,
    source_lum: str | None = None,
    plan_path: str | None = None,
    feature_title: str = "",
) -> list[PlanCitation]:
    """Plan-sourced test citations that overlap feature needles (verified plans first)."""
    records = plan_records_for_feature(
        feat_needles,
        records,
        source_lum=source_lum,
        plan_path=plan_path,
        feature_title=feature_title,
    )
    hits: list[PlanCitation] = []
    seen: set[tuple[str | None, str]] = set()
    needle_l = [n.lower() for n in feat_needles if n and len(n) >= 3]

    def score_rec(rec: PlanRecord) -> int:
        s = 0
        if rec.verified:
            s += 4
        if rec.lum_id and any(rec.lum_id.lower() in n for n in needle_l):
            s += 6
        for n in needle_l:
            if any(n in p.lower() for p in rec.api_paths):
                s += 3
        return s

    ranked = sorted(records, key=score_rec, reverse=True)
    for rec in ranked:
        if score_rec(rec) < 3:
            continue
        for cite in rec.citations:
            path_l = cite.rel_path.lower()
            name_l = (cite.test_name or "").lower()
            if not any(n in path_l or n in name_l for n in needle_l):
                continue
            key = (cite.test_name, cite.rel_path)
            if key in seen:
                continue
            seen.add(key)
            hits.append(cite)
    return hits


def resolve_citation(
    cite: PlanCitation, test_index: list[tuple[str, str, str]]
) -> tuple[str, str] | None:
    """Map plan citation to (rel_path, test_name) in repo test index."""
    path_l = cite.rel_path.lower()
    file_matches: list[tuple[str, str]] = []

    def norm(rel: str) -> bool:
        rl = rel.lower()
        return rl == path_l or rl.endswith("/" + Path(path_l).name.lower())

    for rel, name, body in test_index:
        if not norm(rel):
            continue
        if cite.test_name:
            if cite.test_name == name or cite.test_name in body:
                return rel, cite.test_name
        else:
            file_matches.append((rel, name))

    if file_matches:
        return file_matches[0]

    stem = Path(cite.rel_path).stem
    for rel, name, _ in test_index:
        if stem.lower() in (name.lower(), Path(rel).stem.lower()):
            return rel, name

    full = ROOT / cite.rel_path
    if not full.is_file():
        return None
    body = full.read_text(encoding="utf-8", errors="replace")
    if cite.test_name and cite.test_name.startswith("test_"):
        if re.search(rf"def {re.escape(cite.test_name)}\b", body):
            return cite.rel_path, cite.test_name
        return None
    m = re.search(r"^\s*def (test_[a-zA-Z0-9_]+)", body, re.M)
    if m:
        return cite.rel_path, m.group(1)
    return None


def plan_index_summary(records: list[PlanRecord]) -> dict[str, int]:
    return {
        "plans_total": len(records),
        "plans_active": sum(1 for r in records if r.active),
        "plans_archived": sum(1 for r in records if not r.active),
        "plans_verified": sum(1 for r in records if r.verified),
        "plans_with_citations": sum(1 for r in records if r.citations),
        "citations_total": sum(len(r.citations) for r in records),
    }
