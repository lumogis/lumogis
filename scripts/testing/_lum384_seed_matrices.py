#!/usr/bin/env python3
"""LUM-384/428: seed matrices from code audit + active/archived plan evidence."""
from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from _lum384_plan_audit import (
    PlanRecord,
    load_plan_index,
    lums_for_needles,
    plan_citations_for_feature,
    plan_index_summary,
    resolve_citation,
    supplemental_features,
)

ROOT = Path(__file__).resolve().parents[2]
try:
    SHA = (
        subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True)
        .strip()
    )
except (subprocess.CalledProcessError, FileNotFoundError):
    SHA = "unknown"
DATE = "2026-06-04"
AUDIT = "LUM-428 code + plan audit"

LEGEND = """## Legend

| Symbol | Meaning |
| --- | --- |
| ✅ | At least one test **asserts** the behaviour (see **Notes**) |
| 🟡 | Related tests exist; dedicated assertion for this feature not confirmed at seed |
| ❌ | No matching automated test found in code audit |
| 🚫 | Not automatable — manual checklist row (`MS-###` in `docs/RELEASE-MANUAL-CHECKLIST.md`) |

**Maintenance:** v1 baseline **LUM-384**; **LUM-428** strict citations + plan cross-check. Ongoing via **`/verify-plan`**.

**Evidence:** ✅ = `` `test_name` in `file` `` in repo; may cite **plan** (`LUM-###` / `.cursor/plans/…`). Plans: active + archived.
"""

HEADER_TMPL = (
    f"<!-- Last audited: {DATE} against {SHA} on dev — {AUDIT} -->\n\n"
    "# TEST-COVERAGE-MATRIX — {title}\n\n{legend}\n"
)

# Features derived from capabilities.md + route/UI audit (not one row per HTTP route).
CURATED: list[tuple[str, str, str, list[str], str, bool]] = [
    # matrix, section, feature, needles (path/token), layer, automatable
    # --- Core 1.1 Platform ---
    ("core", "1.1", "Health / readiness (`/healthz`)", ["/healthz", "healthz"], "unit", True),
    ("core", "1.1", "Stack-control supervision", ["stack-control/test_main", "stack_control"], "unit", True),
    ("core", "1.1", "Compose policy guard (LUM-43)", ["check_compose_policy", "compose_policy"], "unit", True),
    ("core", "1.1", "Inbox folder-watch auto-ingest (LUM-330)", ["inbox", "inbox_watcher", "inbox_enqueue"], "integration", True),
    ("core", "1.1", "Multi-path ingest compose binds (LUM-401)", ["ingest_paths", "compose_ingest_binds"], "unit", True),
    ("core", "1.1", "Debug test inventory (LUM-377)", ["inventory.tsv", "test_inventory", "test-list"], "release-rc", True),
    # --- Core 1.2 Ingest ---
    ("core", "1.2", "Admin settings ingest_paths GET/PUT", ["ingest_paths_settings", "/api/v1/admin/settings"], "unit", True),
    ("core", "1.2", "Legacy + v1 data ingest", ["test_api_v1_ingest", "/data/ingest", "ingest_upload"], "unit", True),
    ("core", "1.2", "Ingest upload API", ["test_api_v1_ingest_upload", "ingest/upload"], "unit", True),
    ("core", "1.2", "Ingest path watcher", ["ingest_paths_watcher"], "unit", True),
    ("core", "1.2", "Admin browse / review-queue", ["review-queue", "admin/browse"], "unit", True),
    # --- Core 1.3 Signals / notifications ---
    ("core", "1.3", "Signal sources CRUD", ["test_signals", "/signals/sources"], "unit", True),
    ("core", "1.3", "Signal profile + feedback", ["signals/profile", "signals/feedback"], "unit", True),
    ("core", "1.3", "Routines approve/run", ["routines", "/actions"], "unit", True),
    ("core", "1.3", "SSE `/events` stream", ["test_events", "/api/v1/events", "sse"], "unit", True),
    ("core", "1.3", "Notifications API", ["api_v1/notifications", "test_notifications"], "unit", True),
    # --- Core 1.4 Capture & voice ---
    ("core", "1.4", "Captures ledger CRUD (`/api/v1/captures`)", ["test_api_v1_captures", "/api/v1/captures"], "unit", True),
    ("core", "1.4", "Capture attachments", ["captures", "attachments"], "unit", True),
    ("core", "1.4", "Voice transcribe STT", ["voice_transcribe", "/api/v1/voice"], "unit", True),
    ("core", "1.4", "Quick capture web route (client)", ["QuickCapture", "/capture", "lumogis-web"], "web", True),
    # --- Core 1.5 Search & memory ---
    ("core", "1.5", "Memory semantic search", ["test_memory_search", "memory_search"], "unit", True),
    ("core", "1.5", "Recent sessions API", ["memory/recent", "test_api_v1_memory", "test_recent"], "unit", True),
    ("core", "1.5", "Entity extraction + listing", ["entities/extract", "test_entities_extract", "test_entities"], "unit", True),
    ("core", "1.5", "Qdrant user_id filter isolation", ["user_id", "qdrant", "test_qdrant"], "unit", True),
    ("core", "1.5", "Auto-RAG injection (LUM-308)", ["auto_rag", "AUTO_RAG"], "unit", True),
    ("core", "1.5", "Data search (`/data/search`)", ["/data/search", "test_data"], "unit", True),
    # --- Core 1.6 Auth & credentials ---
    ("core", "1.6", "JWT auth login/refresh", ["test_login", "test_refresh", "test_auth_phase1"], "unit", True),
    ("core", "1.6", "Bootstrap admin", ["test_bootstrap", "bootstrap_admin"], "unit", True),
    ("core", "1.6", "Credential tiers + resolver", ["credential_tier", "credential_tiers"], "unit", True),
    ("core", "1.6", "Connector credentials (me + admin)", ["connector_credentials"], "unit", True),
    ("core", "1.6", "Connector permissions per-user", ["connector_permissions"], "unit", True),
    ("core", "1.6", "Admin users + sessions", ["test_admin_users", "admin_users"], "unit", True),
    ("core", "1.6", "User data export ZIP", ["user_export"], "unit", True),
    ("core", "1.6", "CSRF / origin check on refresh", ["csrf_origin", "csrf_origin_check"], "unit", True),
    # --- Core 1.7 MCP & catalog ---
    ("core", "1.7", "MCP tokens mint/revoke", ["mcp_tokens", "lmcp_"], "unit", True),
    ("core", "1.7", "MCP bearer wiring", ["mcp_bearer", "mcp/probe"], "unit", True),
    ("core", "1.7", "Me tools catalog read model", ["api_v1_me_tools", "me/tools"], "unit", True),
    ("core", "1.7", "Capabilities HTTP registry", ["test_capabilities_route", "routes/capabilities"], "unit", True),
    ("core", "1.7", "Tool catalog LLM merge", ["tool_catalog", "TOOL_CATALOG"], "unit", True),
    # --- Core 1.8 Agentic ---
    ("core", "1.8", "Chat ask + tool loop", ["test_chat", "/ask", "run_tool"], "unit", True),
    ("core", "1.8", "OpenAI-compatible `/v1/chat/completions`", ["test_chat_completions", "chat_completions"], "unit", True),
    ("core", "1.8", "Conversations history API (LUM-162)", ["api_v1_conversations", "conversations"], "unit", True),
    ("core", "1.8", "Approvals pending API", ["approvals", "api_v1/approvals"], "unit", True),
    ("core", "1.8", "Action audit + reverse", ["test_actions", "/audit"], "unit", True),
    ("core", "1.8", "Action proposals atomic claim (LUM-123)", ["action_proposals", "proposal"], "unit", True),
    # --- Core 1.9 Operator ---
    ("core", "1.9", "Doctor CLI", ["test_doctor_cli", "doctor"], "unit", True),
    ("core", "1.9", "Admin diagnostics + stack status (LUM-178)", ["admin_diagnostics", "stack_status"], "unit", True),
    ("core", "1.9", "OpenAPI snapshot + codegen gate", ["openapi_snapshot", "openapi-check"], "unit", True),
    ("core", "1.9", "Public export hygiene", ["check_public_export"], "unit", True),
    ("core", "1.9", "Phase-3 grep security gate", ["phase3_grep"], "unit", True),
    ("core", "1.9", "verify-public-rc release umbrella", ["verify-public-rc", "integration-public-rc"], "release-rc", False),
    ("core", "1.9", "GHCR publish smoke", ["publish-ghcr", "ghcr"], "release-rc", False),
    # --- Web 2.x (from App.tsx audit) ---
    ("web", "2.1", "Me profile", ["MeProfile", "/me/profile"], "web", True),
    ("web", "2.1", "Me connectors", ["MeConnectors", "/me/connectors"], "web", True),
    ("web", "2.1", "Me connector permissions", ["MePermissions", "/me/permissions"], "web", True),
    ("web", "2.1", "Me LLM providers", ["MeLlmProviders", "/me/llm-providers"], "web", True),
    ("web", "2.1", "Me MCP tokens", ["MeMcpTokens", "/me/mcp-tokens"], "web", True),
    ("web", "2.1", "Me notifications", ["MeNotifications", "/me/notifications"], "web", True),
    ("web", "2.1", "Me export", ["MeExport", "/me/export"], "web", True),
    ("web", "2.1", "Me tools/capabilities overview", ["MeToolsCapabilities", "tools-capabilities"], "web", True),
    ("web", "2.2", "Admin users", ["AdminUsers", "/admin/users"], "web", True),
    ("web", "2.2", "Admin connector credentials", ["AdminConnectorCredentials"], "web", True),
    ("web", "2.2", "Admin connector permissions", ["AdminConnectorPermissions"], "web", True),
    ("web", "2.2", "Admin MCP tokens", ["AdminMcpTokens"], "web", True),
    ("web", "2.2", "Admin audit log", ["AdminAudit", "/admin/audit"], "web", True),
    ("web", "2.2", "Admin diagnostics", ["AdminDiagnostics", "/admin/diagnostics"], "web", True),
    ("web", "2.2", "Admin system status (LUM-178)", ["AdminSystemStatus", "system-status"], "web", True),
    ("web", "2.3", "Chat page + stream", ["ChatPage", "ChatStream", "/chat"], "web", True),
    ("web", "2.3", "Search page", ["SearchPage.test", "SearchPage", "/search"], "web", True),
    ("web", "2.3", "Approvals page", ["ApprovalsPage", "/approvals"], "web", True),
    ("web", "2.3", "Conversation history UI (LUM-162)", ["ConversationSidebar", "chat-conversation-history"], "e2e", True),
    ("web", "2.3", "First-wow gating (LUM-216)", ["WowGate", "wow_path_gating"], "e2e", True),
    ("web", "2.3", "Onboarding dismiss persists", ["OnboardingModal", "onboarding_dismiss"], "e2e", True),
    ("web", "2.4", "PWA manifest + service worker", ["manifest.test", "serviceWorker"], "web", True),
    ("web", "2.4", "Web push browser flow", ["webPushBrowser", "web_push"], "web", True),
    ("web", "2.4", "Capture outbox / offline queue", ["captureOutbox", "outbox"], "web", True),
    ("web", "2.4", "Mobile shell e2e", ["mobile_shell", "me_admin_mobile"], "e2e", True),
    ("web", "2.4", "Admin shell e2e", ["admin_shell"], "e2e", True),
    # --- KG 3.x ---
    ("kg", "3.1", "KG API search + entities", ["api_v1/kg", "test_api_v1_kg"], "integration", True),
    ("kg", "3.1", "GRAPH_MODE fallback behaviour", ["graph_mode", "GRAPH_MODE"], "unit", True),
    ("kg", "3.1", "Premium graph query tests", ["premium/test_graph"], "integration", True),
    ("kg", "3.1", "Graph writer pipeline", ["test_graph_writer"], "unit", True),
    ("kg", "3.1", "Admin KG settings + weekly job", ["kg/settings", "trigger-weekly"], "unit", True),
    ("kg", "3.2", "lumogis-graph service tests", ["services/lumogis-graph/tests"], "unit", True),
    ("kg", "3.2", "Graph webhook secret", ["GRAPH_WEBHOOK", "webhook_secret"], "unit", True),
    ("kg", "3.2", "Entity merge/dedup admin", ["entities/merge", "entities/deduplicate"], "unit", True),
    ("kg", "3.2", "Graph parity inprocess vs service", ["graph_parity", "test-graph-parity"], "integration", True),
    ("kg", "3.2", "Mock capability contract", ["mock-capability", "mock_capability"], "unit", True),
    # --- Desktop 4.x ---
    ("desktop", "4.1", "Memory search overlay (LUM-329)", ["searchClient.test", "overlayUi.test"], "web", True),
    ("desktop", "4.1", "Keychain session storage", ["auth.rs", "keychain"], "unit", True),
    ("desktop", "4.1", "Overlay ingest paths (LUM-397)", ["ingest_paths", "lumogis-search"], "unit", True),
    ("desktop", "4.1", "Search overlay build (LUM-430)", ["search-build", "lumogis-search"], "unit", True),
    ("desktop", "4.2", "Search overlay Vitest suite", ["lumogis-search/ui", "overlayUi.test", "searchClient.test"], "web", True),
    ("desktop", "4.2", "Tauri cargo unit tests", ["src-tauri", "cargo test"], "unit", True),
    ("desktop", "4.2", "Global hotkey / tray", ["hotkey", "tray"], "unit", True),
    ("desktop", "4.2", "macOS notarised build", ["notar"], "manual", False),
    ("desktop", "4.2", "Windows signed build", ["signing", "trusted-signing"], "manual", False),
]


@dataclass
class Feature:
    matrix: str
    section: str
    feature: str
    needles: list[str]
    layer: str
    automatable: bool = True
    source_lum: str | None = None
    from_plan: bool = False
    plan_path: str | None = None


def route_audit_extras() -> list[Feature]:
    """Add features for major `/api/v1` mounts if missing from curated list."""
    extras: list[Feature] = []
    text = "\n".join(
        f.read_text(encoding="utf-8", errors="replace")
        for f in (ROOT / "orchestrator/routes").rglob("*.py")
    )
    checks = [
        ("core", "1.6", "Scope publish endpoints", "/publish", ["scope", "publish"]),
        ("core", "1.8", "Me onboarding API", "/me/onboarding", ["onboarding", "me/onboarding"]),
        ("core", "1.6", "Me LLM providers API", "/me/llm-providers", ["llm_providers", "llm-providers"]),
    ]
    existing = {c[2].lower() for c in CURATED}
    for matrix, sec, name, path, needles in checks:
        if path in text and name.lower() not in existing:
            extras.append(Feature(matrix, sec, name, needles, "unit"))
    return extras


def index_tests() -> list[tuple[str, str, str]]:
    index: list[tuple[str, str, str]] = []
    for base in [
        ROOT / "orchestrator/tests",
        ROOT / "stack-control",
        ROOT / "scripts/tests",
        ROOT / "clients/lumogis-web/tests",
        ROOT / "clients/lumogis-search/ui",
        ROOT / "services/lumogis-graph/tests",
    ]:
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if "node_modules" in f.parts:
                continue
            if f.suffix == ".py" and (f.name.startswith("test_") or f.name == "test_main.py"):
                text = f.read_text(encoding="utf-8", errors="replace")
                rel = f.relative_to(ROOT).as_posix()
                for m in re.finditer(r"^def (test_[a-zA-Z0-9_]+)", text, re.M):
                    index.append((rel, m.group(1), text))
                for m in re.finditer(r"^\s+def (test_[a-zA-Z0-9_]+)", text, re.M):
                    index.append((rel, m.group(1), text))
            elif f.suffix in (".ts", ".tsx") and (".test." in f.name or ".spec." in f.name):
                text = f.read_text(encoding="utf-8", errors="replace")
                rel = f.relative_to(ROOT).as_posix()
                index.append((rel, f.stem, text))
    return index


@dataclass
class MatchScore:
    total: int
    path_hit: bool
    name_hit: bool


def score_match_detailed(feat: Feature, rel: str, name: str, body: str) -> MatchScore:
    path_l = rel.lower()
    name_l = name.lower()
    total = 0
    path_hit = False
    name_hit = False
    for n in feat.needles:
        nl = n.lower().strip("/")
        if not nl or len(nl) < 3:
            continue
        if nl in path_l:
            total += 10
            path_hit = True
        elif nl.replace("_", "") in path_l.replace("_", ""):
            total += 6
            path_hit = True
        if nl in name_l:
            total += 8
            name_hit = True
        if nl in body.lower():
            total += 2
    return MatchScore(total=total, path_hit=path_hit, name_hit=name_hit)


def _is_web_ui_feature(feat: Feature) -> bool:
    return feat.matrix == "web" and any(
        n and n[0].isupper() for n in feat.needles if n and n[0].isalpha()
    )


def _required_name_tokens(feat: Feature) -> list[str]:
    title = feat.feature.lower()
    tokens: list[str] = []
    if "login" in title or "jwt" in title:
        tokens.extend(["login", "refresh"])
    if "recent session" in title:
        tokens.append("recent")
    if "sse" in title or "`/events`" in feat.feature:
        tokens.extend(["events", "sse"])
    if "signal source" in title:
        tokens.append("signal")
    if "notification" in title:
        tokens.append("notification")
    if "entity extraction" in title or "entity merge" in title:
        tokens.extend(["extract", "entities", "merge", "dedup"])
    if "graph writer" in title:
        tokens.append("writer")
    if "admin user" in title and "session" in title:
        tokens.extend(["admin_user", "admin_users"])
    if "quick capture" in title and feat.layer == "web":
        tokens.extend(["quick", "capture"])
    if "search page" in title:
        tokens.extend(["searchpage", "search_page"])
    return tokens


def _strict_green(feat: Feature, rel: str, name: str, ms: MatchScore) -> tuple[bool, str]:
    if ms.total < 12 or not (ms.path_hit or ms.name_hit):
        return False, "weak path/name match (LUM-428)"
    if _is_web_ui_feature(feat) and "lumogis-web" not in rel:
        return False, "orchestrator/API only — not web UI (LUM-428)"
    if feat.feature.startswith("Quick capture") and "lumogis-web" not in rel:
        return False, "backend capture API only — not web route (LUM-428)"
    req = _required_name_tokens(feat)
    name_l = name.lower()
    if req and not any(t.replace("_", "") in name_l.replace("_", "") for t in req):
        return False, f"`{name}` missing feature token ({', '.join(req[:3])})"
    if feat.matrix == "desktop" and feat.layer == "web" and "lumogis-search" not in rel:
        return False, "not a desktop/search client test (LUM-428)"
    return True, ""


def _test_body(rel: str) -> str:
    p = ROOT / rel
    if p.is_file():
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


def _try_plan_match(
    feat: Feature,
    test_index: list[tuple[str, str, str]],
    plan_records: list[PlanRecord],
) -> tuple[str, str, str] | None:
    """Prefer verified-plan citations when they resolve in the repo."""
    for cite in plan_citations_for_feature(
        feat.needles,
        plan_records,
        source_lum=feat.source_lum,
        plan_path=feat.plan_path,
        feature_title=feat.feature,
    ):
        resolved = resolve_citation(cite, test_index)
        if not resolved:
            continue
        rel, name = resolved
        body = _test_body(rel)
        if cite.test_name and cite.test_name.startswith("test_"):
            if not re.search(rf"def {re.escape(cite.test_name)}\b", body):
                continue
        ms = score_match_detailed(feat, rel, name, body)
        if cite.test_name and cite.test_name == name:
            ms = MatchScore(total=ms.total + 14, path_hit=True, name_hit=True)
        elif cite.test_name:
            continue
        if ms.total < 10:
            continue  # plan path mentioned but not feature-relevant
        green, reason = _strict_green(feat, rel, name, ms)
        note = f"`{name}` in `{rel}`"
        plan_ref = feat.plan_path or feat.source_lum or "plan"
        if green:
            return "✅", rel, f"{note}; plan `{plan_ref}` ({cite.source})"
        if ms.total >= 10:
            return "🟡", rel, f"{note}; plan partial — {reason}"
    return None


def match_tests(
    feat: Feature,
    test_index: list[tuple[str, str, str]],
    plan_records: list[PlanRecord],
) -> tuple[str, str, str]:
    if not feat.automatable:
        return "🚫", "—", "MS-TBD"
    plan_hit = _try_plan_match(feat, test_index, plan_records)
    if plan_hit:
        return plan_hit
    if feat.from_plan:
        return (
            "❌",
            "—",
            f"no qualifying test citation in plan `{feat.plan_path or feat.source_lum}`",
        )
    best: tuple[MatchScore, str, str] | None = None
    for rel, name, body in test_index:
        ms = score_match_detailed(feat, rel, name, body)
        if ms.total >= 8 and (best is None or ms.total > best[0].total):
            best = (ms, rel, name)
    if not best:
        return "❌", "—", "code audit: no test match"
    ms, rel, name = best
    if rel.endswith(".py") and name.startswith("test_"):
        if name not in (ROOT / rel).read_text(encoding="utf-8", errors="replace"):
            return "🟡", rel, f"file match; verify `{name}`"
    green, reason = _strict_green(feat, rel, name, ms)
    note = f"`{name}` in `{rel}`"
    if green:
        return "✅", rel, f"{note}; code audit"
    return "🟡", rel, f"{note}; {reason}"


def lums_for_feat(feat: Feature, plan_records: list[PlanRecord]) -> str:
    if feat.source_lum:
        return feat.source_lum
    found = lums_for_needles(feat.needles, plan_records)
    return ", ".join(found[:3])


def assign_ids(features: list[Feature]) -> list[tuple[Feature, str]]:
    counters: dict[str, int] = defaultdict(int)
    out: list[tuple[Feature, str]] = []
    for f in features:
        counters[f.section] += 1
        out.append((f, f"{f.section}.{counters[f.section]}"))
    return out


def render_matrix(
    title: str,
    rows: list[tuple[Feature, str, str, str, str]],
    plan_records: list[PlanRecord],
) -> str:
    body = HEADER_TMPL.format(title=title, legend=LEGEND)

    def sec_key(id_: str) -> tuple[int, int]:
        p = id_.split(".")
        return (int(p[0]), int(p[1]))

    rows = sorted(rows, key=lambda r: (sec_key(r[1]), int(r[1].split(".")[-1])))
    body += "| ID | Feature | Test source(s) | Layer | Status | Notes |\n| --- | --- | --- | --- | --- | --- |\n"
    cur = None
    for feat, id_, status, src, note in rows:
        sec = ".".join(id_.split(".")[:2])
        if sec != cur:
            body += f"\n### §{sec}\n\n"
            cur = sec
        lum = lums_for_feat(feat, plan_records)
        notes = f"{lum}; {note}" if lum else note
        body += f"| {id_} | {feat.feature} | {src} | {feat.layer} | {status} | {notes} |\n"
    return body


def pad(features: list[Feature], matrix: str, section: str, target: int) -> list[Feature]:
    cur = [f for f in features if f.matrix == matrix]
    if len(cur) >= target:
        return features
    out = list(features)
    for i in range(target - len(cur)):
        out.append(
            Feature(matrix, section, f"Audit gap — feature TBD ({i + 1})", [], "unit", True)
        )
    return out


def main() -> None:
    plan_records = load_plan_index()
    summary = plan_index_summary(plan_records)
    print("plan_index:", summary)

    features = [
        Feature(m, s, f, n, layer, auto)
        for m, s, f, n, layer, auto in CURATED
    ]
    features.extend(route_audit_extras())
    existing_titles = {f.feature.lower() for f in features}
    for m, s, label, needles, layer, auto, lum, ppath in supplemental_features(
        plan_records, existing_titles
    ):
        features.append(
            Feature(
                m,
                s,
                label,
                needles,
                layer,
                auto,
                source_lum=lum,
                from_plan=True,
                plan_path=ppath,
            )
        )
    features = pad(features, "core", "1.9", 50)
    features = pad(features, "web", "2.4", 25)
    features = pad(features, "kg", "3.2", 20)
    features = pad(features, "desktop", "4.2", 15)

    test_index = index_tests()
    buckets: dict[str, list[tuple[Feature, str, str, str, str]]] = defaultdict(list)
    for feat, id_ in assign_ids(features):
        st, src, note = match_tests(feat, test_index, plan_records)
        buckets[feat.matrix].append((feat, id_, st, src, note))

    (ROOT / "docs/testing").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs/private/testing").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs/testing/TEST-COVERAGE-MATRIX-core.md").write_text(
        render_matrix("Core (orchestrator & platform)", buckets["core"], plan_records),
        encoding="utf-8",
    )
    (ROOT / "docs/testing/TEST-COVERAGE-MATRIX-web.md").write_text(
        render_matrix("Web (lumogis-web)", buckets["web"], plan_records),
        encoding="utf-8",
    )
    (ROOT / "docs/private/testing/TEST-COVERAGE-MATRIX-kg.md").write_text(
        render_matrix("Knowledge Graph (private checkout)", buckets["kg"], plan_records),
        encoding="utf-8",
    )
    (ROOT / "docs/private/testing/TEST-COVERAGE-MATRIX-desktop.md").write_text(
        render_matrix("Desktop (private checkout)", buckets["desktop"], plan_records),
        encoding="utf-8",
    )

    for k, v in buckets.items():
        ids = [r[1] for r in v]
        assert len(ids) == len(set(ids)), f"dup ids in {k}"
    print({k: len(v) for k, v in buckets.items()})


if __name__ == "__main__":
    main()
