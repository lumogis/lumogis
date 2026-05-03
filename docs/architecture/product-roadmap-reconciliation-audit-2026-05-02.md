# Product roadmap reconciliation audit — 2026-05-02

**Kind:** Read-only audit. No code, portfolio, or doc moves.  
**Auditor:** Composer (agent pass).

**Note:** References to `.cursor/` (plans, follow-up portfolio, skills such as `/verify-plan`) describe the **private** monorepo and Cursor devtools layout. The public AGPL snapshot omits `.cursor/`; finalized decisions remain under `docs/decisions/`.

---

## 1. Executive summary

**What is truthful today**

- **Cross-device Web** has substantial shipped surface: `/api/v1` façade, Lumogis Web client, Phase 2 mobile UX extraction verified, Phase 3 PWA/SW boundary per ADR 030 history, Phase 4 Web Push (4A–4E) closed with **FP-053** deferral for `ACTION_EXECUTED`→push, Phase 5 Capture MVP (5A–5I) closed per architecture extraction docs.
- **Household multi-user**, **per-user credentials**, **MCP tokens**, **structured audit**, **password foundation (non-email)**, **capability scaffolding**, **STT façade + sidecar path (FP-054 done)**, and **graph admin/KG** work appear in code and recent CHANGELOG entries.
- **`semantic_search`** still targets Qdrant collection **`documents` only**; capture index path writes **`conversations`** (and notes) per plan — so **indexed captures do not appear in memory search** until **FP-TBD-5.1**-class work ships (**code evidence:** `orchestrator/services/search.py`).

**What is misleading without caveats**

- **`.cursor/follow-up-portfolio.md`** is **merge-driven**, not scraped from `docs/`. **Leverage sort favours old BL-seeded rows** (FP-014, FP-042, FP-009…). It **does not list** Capture plan **§21 `FP-TBD-5.*`** as distinct FP rows.
- **`docs/LUMOGIS_REFERENCE_MANUAL.md` §17** (as of 2026-04-26 stamp) says Phase 2 is **Next** and parent 3–5 **Open** — **stale vs** `docs/architecture/cross-device-web-phase-5-capture-plan.md` (Phase 2–4 closed; Phase 5 MVP closed).
- **`lumogis-web-roadmap-reconciliation-after-remediation.md` §5** still lists parent Phase 2/4/5 as open in a snapshot table — **stale vs** later extraction closes.
- **`.cursor/plans/cross_device_lumogis_web.plan.md` frontmatter** still says **Phases 2–6 open** — **conflicts** with extraction docs that closed 2–5 slices (Phase 6 / Tauri still legitimately open).

**Recommended pragmatic “next product chunk” (single headline)**

1. **Capture ↔ memory search parity** (`FP-TBD-5.1`): user-visible gap (indexed captures invisible in semantic search).

**Then** (order depends on operator vs product emphasis)

2. **Web Push completion:** **FP-053** safe templates for `ACTION_EXECUTED` (or explicit drop with ADR note).  
3. **Runtime credential resolution** (**FP-009**) for CalDAV/ntfy/LLM consistency.  
4. **Per-user LLM product surface** (**FP-014**): Me LLM view is **read-only** in code today.  
5. **Batch jobs / admin diagnostics depth** (**FP-012** + LWAS-4 class gap from reconciliation doc).

---

## 2. Product state map

Tracks align with the audit brief. **Evidence** is one sentence each (code, ADR, or doc).

| Track | State | Evidence |
| --- | --- | --- |
| Cross-device web foundation | **Closed as MVP** | `orchestrator/routes/api_v1/__init__.py` aggregates v1 façade; Caddy same-origin documented in cross-device plan logs. |
| Lumogis Web / PWA / mobile UX | **Closed as MVP** | Phase 2 + 3 + 4 extraction docs report closed; client `App.tsx`, `clients/lumogis-web/src/pwa/`. |
| Auth/session/security | **Partial** | JWT/session + CSRF patterns shipped; **FP-006/007/008** remain for revocation, shared rate limit, optional CSRF. |
| Web Push / notifications / ntfy | **Partial** | Push + SW paths shipped; **FP-053** open; ntfy parallel — **FP-028**. |
| Capture metadata | **Closed as MVP** | `orchestrator/routes/api_v1/captures.py` CRUD + OpenAPI models. |
| Capture attachments/media | **Closed as MVP** | Multipart attach + download in `captures.py`; 501 on legacy upload path per plan. |
| STT / voice input | **Partial** | `voice.py` + `transcribe` on captures + `services/speech_to_text`; **FP-055** in-process faster_whisper adapter open. |
| Capture indexing / file index / audio memos | **Partial** | `POST …/index` to `notes` + Qdrant **conversations**; **no** `documents` upsert — **FP-TBD-5.1**; **FP-023** file index debt. |
| LLM provider credentials / per-user LLM | **Partial** | `GET /api/v1/me/llm-providers` + **read-only** `MeLlmProvidersView.tsx`; **FP-014/015** open. |
| Credential scopes / runtime resolution | **Open** | **FP-009/011**; ADR 027 scope. |
| Connector permissions | **Partial** | ADR 024 + routes; **FP-016** follow-ups. |
| MCP | **Partial** | Tokens/routes exist; **FP-019/020/029** open. |
| Capability / OOP tools | **Closed as MVP (scaffold)** | Registry + mock; **FP-048–051** productisation. |
| Tool catalog / `/api/v1/me/tools` | **Partial** | `routes/me.py` `/tools`; façade + permission labels; **FP-051** richer errors. |
| Agentic execution / Ask-Do / audit | **Partial** | Ask-do safety ADR 006; capability audit fan-in per Phase 5 docs — not fully audited here. |
| Batch jobs / queues | **Partial** | `batch_queue` enqueue in `routes/data.py`; **FP-012** admin/queue depth gaps. |
| Deep Research | **Deferred / draft** | **FP-037**; no `deep_research` symbol in orchestrator **grep** this pass. |
| Web search | **Deferred / draft** | **FP-038**. |
| Memory scopes | **Partial** | ADR 015; **FP-021/022**. |
| Entities / relations | **Partial** | **FP-024** revisit. |
| Knowledge graph / extraction | **Partial** | Admin `/graph/*` routes; **FP-031/042**; Falkor + optional `lumogis-graph` service in CHANGELOG. |
| Backups / export / erasure | **Partial** | ADR 016; **FP-017/018** open. |
| Password management | **Closed as MVP** | ADR 029 foundation shipped; **FP-052** email reset revisit. |
| Licensing / AGPL hygiene | **Partial** | ADR 032; **FP-057** doc sync open. |
| CI/test hardening | **Partial** | **FP-047** optional CI e2e; Vitest/Playwright exist per portfolio Done **FP-046**. |
| Multi-tenant / hosted future | **Deferred** | **FP-004/045/043**. |

---

## 3. Source inventory

### 3.1 Counts (paths)

| Bucket | Count | Notes |
| --- | ---: | --- |
| `.cursor/plans/*.plan.md` | **34** | Includes cross_device, voice, KG, agentic_core, credential waves, etc. |
| `docs/architecture/*.md` | **14** | Plans, remediation, closeouts, `agentic_core.md`, `plugin-imports.md`, … |
| `docs/decisions/*.md` | **33** | ADRs + `DEBT.md` |
| `docs/backlog/*.md` | **1** | `repo-followup-backlog.md` (BL seed for FP-001–045) |
| `.cursor/adrs/*.md` | **32** | Often mirrors decisions / drafts |
| `.cursor/explorations/*.md` | **33** | Retros + spikes (private devtools; count from audit run) |

**Topics index:** **0** files matching `**/topics/**/*.md` under repo root (not present).

**Total paths enumerated for this audit:** **147** markdown files across the six buckets above (thematic overlap between `adrs` and `explorations`; **~85 unique “planning topics”** if deduped by subject, not performed mechanically).

### 3.2 High-signal sources (abbreviated)

| Path | Apparent status | Follow-ups |
| --- | --- | --- |
| `.cursor/plans/cross_device_lumogis_web.plan.md` | Frontmatter: **Phases 2–6 open** (stale vs extractions) | Plan slug register (415, CSP, webpush cap, …) not promoted to FP |
| `docs/architecture/cross-device-web-phase-2-mobile-ux-plan.md` | **implemented** (YAML) | Proposed portfolio refs only in §10 |
| `docs/architecture/cross-device-web-phase-4-web-push-plan.md` | **Closed 4A–4E** | Manual smoke checklist; **FP-053** |
| `docs/architecture/cross-device-web-phase-5-capture-plan.md` | **closed-mvp** | **§21 FP-TBD-5.1–5.17** — **not in portfolio** |
| `docs/architecture/lumogis-speech-to-text-foundation-plan.md` | STT-2 chunks closed per Phase 5 summary | Cross-links to Capture |
| `docs/architecture/lumogis-web-roadmap-reconciliation-after-remediation.md` | **2026-04-26** | §4 portfolio analysis; **§5 backlog table stale** |
| `docs/LUMOGIS_REFERENCE_MANUAL.md` §17 | **2026-04-26** stamp | **Stale** vs architecture extractions |
| `docs/decisions/030-*.md` | Finalised umbrella | Phase history; deferrals |
| `docs/decisions/DEBT.md` | Open graph stats `user_id=default` | Aligns **FP-042** |
| `.cursor/follow-up-portfolio.md` | **2026-05-01** last touch | 54 open FP rows; **not** doc-scanned |

*(Full per-file extraction omitted for length; closeout reviews: phase-4-household, phase-5-final-capability, phase-5-mock-contract, self-hosted-consolidation.)*

---

## 4. Follow-up portfolio reconciliation

### 4.A Portfolio rows that appear accurate and still open (sample — high confidence)

| FP-ID | Title | Product track | Why still open | Confidence |
| --- | --- | --- | --- | --- |
| FP-009 | resolve_runtime_credential | Credentials | ADR 027 + multi-connector reality; not “done” in single verify | **High** |
| FP-012 | Batch jobs / admin queue | Batch | ADR 025 scope exceeds spot-check | **High** |
| FP-014 | LLM per-user dashboard/e2e | LLM | UI read-only; comment in `MeLlmProvidersView.tsx` | **High** |
| FP-017 | Backup CSRF/erasure | Export | ADR 016 revisit class | **Medium** |
| FP-019 | MCP KG gateway/scopes | MCP | Broad ADR 017 scope | **Medium** |
| FP-042 | Graph stats `user_id=default` | KG | `DEBT.md` + FP notes | **High** |
| FP-048–051 | Capability invoke/auth/copy | Capabilities | Closeout review explicit | **High** |
| FP-053 | ACTION_EXECUTED → push | Notifications | ADR 030 deferral | **High** |
| FP-055 | STT-2D in-process | Voice | Opened post FP-054 | **High** |
| FP-057 | Licence doc SPDX sync | Licensing | ADR 032 revisit | **High** |

### 4.B Portfolio rows that may be stale, completed, duplicated, or mis-scored

| FP-ID | Title | Issue type | Evidence | Proposed action | Confidence |
| --- | --- | --- | --- | --- | --- |
| FP-001 | Cross-device umbrella | **Stale notes / mis-scored L** | Extraction docs closed Phases **2–5** MVP; frontmatter of parent plan still “2–6 open”; FP-001 **L=10** hides huge remaining **product** work buried in Notes | **Merge/split:** keep umbrella; add child FP rows from **FP-TBD-5.***; rescoring via `/verify-plan` | **High** |
| FP-002 | Cross-device open questions | **Partially stale** | Phase 4 now closed in ADR 030 history; blob still useful for non-push topics | **Trim notes** on verify | **Medium** |
| FP-003 | Out-of-scope v1 | **Too vague** | Much “v1” shipped | **Needs human decision:** drop or rewrite scope | **Low** |
| FP-030 | DEFAULT_USER → per-token | **Superseded** | Notes say superseded by 017 | **Close** with retro if code+docs clean | **Medium** |
| FP-014 vs FP-015 | LLM rows | **Possible duplicate theme** | Both ADR 026 family | **Merge or rank** explicitly | **Low** |
| FP-037 | deep_research | **Draft + no code hits** | grep found no implementation symbol | **Lower priority** or **park** until plan | **Medium** |
| FP-027 | Credential Mgmt UX | **Misprioritised vs L** | L=10 with low I in seed era | **Re-grade** after CalDAV/runtime creds | **Medium** |

### 4.C Plan or ADR follow-ups missing from portfolio (proposed rows — **do not add manually**)

| Proposed title (for FP-058+) | Source | Track | Why it matters | I | E | L | Conf. |
| --- | --- | --- | --- | --- | --- | ---: | --- |
| Capture: **`memory/search` parity** for indexed captures (`documents` and/or dedicated collection) | `cross-device-web-phase-5-capture-plan.md` §21 **FP-TBD-5.1** | Capture / memory | User-visible “search my captures” gap | 5 | 3 | **15** | **High** |
| Capture: **explicit memory purge** for indexed capture (notes+Qdrant+graph) | §21 **FP-TBD-5.5** | Capture | Deletes blocked at 409 today | 4 | 3 | 12 | **High** |
| Capture: **`entities_extract` evidence** for capture pipeline | §21 **FP-TBD-5.4** | KG / capture | Consistent audit/KG | 3 | 2 | 12 | **Medium** |
| Capture: **default index policy** UX/settings | §21 **FP-TBD-5.2** | Capture | Consent/product | 3 | 2 | 12 | **Medium** |
| Capture: **offline photo staging** | §21 **FP-TBD-5.10b** | Mobile capture | Deferred MVP | 3 | 3 | 9 | **Medium** |
| Capture: **mobile local / direct STT** track | §21 **FP-TBD-5.15–5.17** | Voice | Privacy/offline transcript | 4 | 4 | 8 | **Medium** |
| Cross-device plan slug bundle: **415 enforcement, rate limit body, dedupe frozenset, action_log index, webpush fanout cap, per-user models in web, conversations persistence, cookie path v2, CSP hash** | `cross_device_lumogis_web.plan.md` follow-up register | Web hardening | Engineering quality / security | 4 | 4 | 8 | **Medium** |
| Admin diagnostics **batch job depth** (LWAS-4) | `lumogis-web-roadmap-reconciliation-after-remediation.md` §2.2 | Ops / batch | Operator visibility | 3 | 3 | 9 | **Medium** |
| Manual **Web Push E2E smoke** execution record | `cross-device-web-phase-4-web-push-plan.md` §23 | QA | Doc says checklist not run in CI | 2 | 1 | 10 | **Low** |
| **Phase 6 Tauri** stub / trigger checklist | ADR 030 + Capture plan next steps | Desktop | Explicit parent phase | 2 | 2 | **8** | **High** (as deferral) |
| **Agentic Core** slice 1: static registry + read-only AI Team page | `docs/architecture/agentic_core.md` | Strategic | Post-capture direction | 4 | 4 | 8 | **Medium** (planning-only) |

### 4.D Commitments with no clear owner row

| Commitment | Source | Why it matters | Suggested owner | Next action |
| --- | --- | --- | --- | --- |
| Indexed captures in **semantic search** | Capture §9 + `search.py` uses **`documents` only** | Broken user expectation | **FP-TBD-5.1** → new FP | Spec + `/verify-plan` |
| LibreChat compose **Pass 4.3** | Phase 4 plan §58 | Product decision | Cross-device / ops | ADR or explicit **dropped** |
| **Graph stats** tenant isolation | `DEBT.md` | Privacy | **FP-042** | Implement or defer Phase 6 |
| **Reference manual §17** accuracy | `LUMOGIS_REFERENCE_MANUAL.md` | Onboarding truth | Docs | Editorial verify pass |

---

## 5. Missing follow-ups (consolidated list)

See **§4.C**. The largest systematic gap is **§21 `FP-TBD-5.*` not promoted** to `FP-058+`. Secondary gap: **cross_device** plan slug register **not promoted**.

---

## 6. Stale / duplicate / possibly completed rows

See **§4.B**. Additionally: **reconciliation doc §5 “immediate gaps”** contradicts shipped capture — treat that doc as **partially obsolete** except portfolio §4 analysis.

---

## 7. Roadmap dependency graph (text)

```
[Runtime credential resolution FP-009] ──┬──► [Connector UX FP-027] [CalDAV FP-025]
                                         └──► [LLM provider integrity FP-014/015]

[Capture index MVP done] ──► [FP-TBD-5.1 memory/search parity] ──► [FP-TBD-5.5 purge] (clean deletes)
                    └──► [FP-TBD-5.4 entity evidence] ──► [KG quality FP-040/041]

[Web Push core done] ──► [FP-053 ACTION_EXECUTED templates] ──► (optional) richer approval templates

[Capability scaffold FP-048–051] ──► (future) real SKU capabilities / Phase 6 marketplace (deferred)

[STT FP-054 done] ──► [FP-055 in-process] (ops simplification)

[MCP FP-019] independent but feeds tool catalog + research drafts FP-037 if revived]

[Agentic Core] blocked on “capture/voice line complete enough” per agentic_core.md — **human gate**
```

---

## 8. Recommended next 5 chunks (practical execution order)

| # | Name | Product outcome | Why now | Dependencies | Key reconcile sources | Not in scope | Acceptance hints | Verify mode |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | **Capture search parity** | Indexed captures discoverable via semantic search (or explicit parallel UX) | Highest user-visible Capture gap | Embedding/Qdrant schema | Phase 5 plan §9 §21 **FP-TBD-5.1**; `search.py` | Full graph extraction | Tests: search returns capture-sourced chunks; OpenAPI if new routes | **New plan + `/verify-plan`** |
| 2 | **Capture memory purge** | User/admin can remove indexed capture from memory without orphan semantics | Unblocks 409 deletes | Chunk 1 design (ordering flexible) | **FP-TBD-5.5** | Cascade policy beyond capture | 409→200 flows; audit | `/verify-plan` |
| 3 | **ACTION_EXECUTED Web Push** | Safe notification when actions complete | Completes Phase 4 story | Template/privacy review | **FP-053**; `webpush.py` | Verbose payloads | Redaction tests; connector ID leakage | `/verify-plan` |
| 4 | **Runtime credential resolution** | CalDAV/ntfy/LLM paths consistent | Unlocks connector reliability | ADR 027 | **FP-009** | Vault broker FP-010 | Integration tests per connector | `/verify-plan` |
| 5 | **Per-user LLM settings product** | Editing keys/models where policy allows | Me shell completeness | Crypto/prefs patterns | **FP-014**; `MeLlmProvidersView` | Full router admin | e2e: set key → chat uses | **`/verify-plan` + e2e** |

---

## 9. Recommended next 10 chunks (6–15)

6. **Batch job admin surface** — **FP-012** + LWAS-4 diagnostics depth.  
7. **Graph stats tenant fix** — **FP-042** / `DEBT.md`.  
8. **MCP hardening** — **FP-019** (gateway, scopes, forensic).  
9. **Capability invoke URL v1** — **FP-048** (manifest-declared invoke).  
10. **KG capability bearer posture** — **FP-049**.  
11. **Connector permissions polish** — **FP-016**.  
12. **Backup/erasure** — **FP-017** CSRF + erasure flows.  
13. **STT-2D in-process** — **FP-055** (single-container ops).  
14. **Optional web e2e CI** — **FP-047**.  
15. **AGPL doc sync** — **FP-057** (SPDX consistency).

*(Deep research FP-037, news FP-035, conversational voice FP-036 intentionally late — draft/low code evidence.)*

---

## 10. Proposed `follow-up-portfolio.md` changes (for `/verify-plan` / `/record-retro` only — **not applied here**)

1. **Add** explicit FP rows (FP-058+) for **FP-TBD-5.1**, **5.5**, **4** (entity evidence), optionally **5.2**, **5.10b**, **5.15–5.17** as a single “Capture Phase 5.2” epic with children if preferred.  
2. **Update FP-001 Notes** to state **Phases 2–5 MVP closed per extraction docs**; remaining: **Phase 6 Tauri**, **Pass 4.3**, **§21 follow-ups**, plan slug bundle.  
3. **Consider closing or rescoring FP-030** after doc/code confirmation.  
4. **Trim FP-002** notes removing “Phase 4 open” language.  
5. **Rescore FP-014** after LLM UI ships (I/E refresh).  
6. **Add** row or bundle for **cross_device plan slug** follow-ups (or tie to **FP-002** with structured checklist).  
7. **Reference manual §17**: editorial — **not** portfolio.

---

## 11. Open questions for Thomas

1. **Search parity:** Should indexed captures land in **`documents`**, a **new collection**, or **only** a dedicated “search captures” API? (Affects FP-TBD-5.1 design.)  
2. **Parent plan as source of truth:** Should `.cursor/plans/cross_device_lumogis_web.plan.md` frontmatter be **updated** to match extraction docs, or are extractions authoritative and the plan **legacy**?  
3. **Agentic Core gate:** What does “capture complete” mean for starting registry/AI Team page — **MVP only** or **§21 cleared**?  
4. **LibreChat Pass 4.3:** Explicitly **in** or **out** for AGPL public narrative?  
5. **Portfolio leverage formula:** Keep **L = I×(6−E)** or add a **“user-visible”** boost so Capture parity beats old BL rows?

---

## 12. Exact commands run and test/search evidence

Commands executed in audit environment:

```bash
cd /mnt/ssd/Cursor/projects/lumogis-app
find .cursor/plans -maxdepth 1 -name '*.plan.md' | wc -l
# → 34

find docs/architecture -maxdepth 1 -name '*.md' | wc -l
# → 14
```

**Note:** `rg` (ripgrep) was **not installed** on PATH in the sandbox (`command not found`). Evidence used **Cursor `Grep`/`Glob` tools** and targeted file reads instead.

**Spot-check reads**

- `orchestrator/routes/api_v1/__init__.py` — v1 routers list.  
- `orchestrator/routes/api_v1/captures.py` — docstring + `/{capture_id}/index` route present.  
- `orchestrator/services/search.py` — `semantic_search` → `collection="documents"`.  
- `clients/lumogis-web/src/features/me/MeLlmProvidersView.tsx` — read-only comment.  
- `orchestrator/routes/me.py` — `/tools` path (line ~139).  
- Grep `deep_research` in `orchestrator/**/*.py` — **no matches**.  
- `.cursor/plans/cross_device_lumogis_web.plan.md` header — status line **Phases 2–6 open**.

---

## 13. Terminal summary (audit digest)

| Metric | Value |
| --- | ---: |
| Planning markdown files enumerated (plans + architecture + decisions + backlog + adrs + explorations) | **147** |
| Product tracks classified | **28** |
| Likely stale / needs-rescore FP rows (sample set §4.B) | **7** |
| Missing follow-ups surfaced for promotion (§4.C table rows) | **11** |
| **Recommended next chunk** | **Capture `memory/search` parity (FP-TBD-5.1 → new FP)** |
| **Audit document path** | `docs/architecture/product-roadmap-reconciliation-audit-2026-05-02.md` |

---

*End of audit. Apply portfolio updates only via `/verify-plan` or `/record-retro` per `.cursor/follow-up-portfolio.md`.*
