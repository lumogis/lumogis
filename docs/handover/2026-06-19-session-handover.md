# Session handover — 2026-06-19 (branch `claude/youthful-carson-84uh2i`)

> Status: Superseded — merged to `dev`
> Last reviewed: 2026-06-24
> Verified against commit: 0a13846
> Superseded by: [`docs/decisions/107-lum-212-211-512-web-ux-loading-errors-health.md`](../decisions/107-lum-212-211-512-web-ux-loading-errors-health.md) (Web cluster) and [`docs/decisions/108-lum-320-doctor-v2-slice-hardening.md`](../decisions/108-lum-320-doctor-v2-slice-hardening.md) (Doctor cluster)
> Notes: Superset handover artefact; retained for merge archaeology. Do not use as the primary implementation reference.

**Branch:** `claude/youthful-carson-84uh2i`
**Base / merge target:** `origin/dev`
**HEAD at handover:** `5e132a4` (this doc adds one more commit on top)
**Scope:** 18 commits, 43 files, ~3,100 insertions vs `dev`.
**Author:** Claude Code (`claude-opus-4-8`).

> For whoever runs the tests / verify-plan / merge (Cursor on a live stack). Everything
> verifiable **without** a running stack is green (unit/contract tests, typecheck, lint,
> plus a live in-process drive of the one new HTTP endpoint). The live-stack gaps are
> called out per cluster in §6.
>
> A deeper web-only handover already exists at
> `docs/handover/2026-06-19-web-ux-cluster-LUM-212-211-512.md` — this document is the
> superset (Doctor cluster + Web cluster + design/recon outcomes).

---

## 1. TL;DR — two clusters + design work

| Cluster | Tickets | Nature | Merge readiness |
|---|---|---|---|
| **A. Doctor hardening** | LUM-337, 340, 341, 343, 344, 494 | Bash CLI + tests + ADRs | Self-contained; tests green in-container |
| **B. Web UX** | LUM-420, 212, 211, 512 | React + 1 new orchestrator endpoint | Reviewed ×3; FE/contract green |
| **Design / recon** | LUM-511 (deferred), LUM-159 (explored), LUM-462 (relationship) | No code — Linear comments + §7 here | n/a |

No DB migrations. No new runtime dependencies. New **env knobs** are all opt-in (§5).

---

## 2. Commit inventory (oldest → newest)

**Cluster A — Doctor:**
- `2375cd9` LUM-494 — restart-loop guard for `compose_restart_service`
- `2e51c5b` LUM-343 — Makefile `doctor-fix` / `-dry` / `-apply` shortcuts
- `8221de3` LUM-337 — ship `jq` in orchestrator image for `doctor --json`
- `1f876fb` LUM-340 — version core-service allowlist (K) via `core-services.json`
- `7a63f78` LUM-494 — test: only applied restart rows advance the guard
- `ad3f6b1` LUM-494/340 — close verify-plan P3 gaps (malformed audit rows, loader precedence)
- `e0eace9` LUM-344 — backfill LUM-320 doctor §Test cases
- `890799d` LUM-341 — slice-2 `.env` safelist **ADR amendment (design only)**
- `7e5c373` LUM-341 — implement slice-2 `.env` config-edit safelist
- `3bdb404` LUM-341 — harden `.env` value charset (holistic-review fix)

**Cluster B — Web UX:**
- `4ecab70` LUM-420 — Playwright mobile smoke (chat sidebar)
- `94e865a` LUM-212 — skeleton/loading primitives + first surfaces
- `970db10` LUM-212 — skeleton for entity detail panel
- `ba2f30d` LUM-211 — ErrorState + AppErrorBoundary
- `0e1b264` LUM-512 — non-admin cached `GET /api/v1/health`
- `1a637fb` LUM-512 — chat service-degradation banners (frontend)
- `98b3c30` LUM-211/512 — address code-review findings (10)
- `5e132a4` docs — web-cluster handover

---

## 3. Run ALL checks (master checklist)

### Frontend (no stack needed)
```bash
cd clients/lumogis-web
npm ci
npm run codegen          # regenerates src/api/generated/openapi.d.ts (gitignored)
npm run lint             # eslint, max-warnings 0
npx tsc -b               # typecheck (project refs incl. e2e specs)
npm run test             # vitest — expect 321 passed (56 files)
```

### Orchestrator unit/contract (needs deps; use the container for the full suite)
```bash
# Full suite with all deps present (canonical):
make compose-test
# Doctor JSON-contract parity (disposable lumogis-test + jq shape check):
make compose-test-doctor
```
Targeted (if running a local venv with deps installed — locally needed `pip install argon2-cffi qdrant-client …`):
```bash
cd orchestrator
python -m pytest tests/test_api_v1_health.py tests/test_stack_status_service.py \
                 tests/test_api_v1_admin_stack_status.py tests/test_api_v1_openapi_snapshot.py -q   # 23 passed
python -m pytest tests/test_doctor_cli.py -q          # doctor suite: 71 passed, 4 skipped
```

### Web e2e (NEEDS live stack + smoke creds)
```bash
export LUMOGIS_WEB_SMOKE_EMAIL=...           # family-LAN smoke user
export LUMOGIS_WEB_SMOKE_PASSWORD='...'      # >= 12 chars
cd clients/lumogis-web && npm run codegen && npm run build
npm run e2e -- chat-degradation chat-sidebar-mobile
# enforce creds (no silent skip): npm run e2e:prove
```

### Doctor on a live stack (NEEDS Compose stack)
```bash
make doctor ARGS="--json"          # JSON contract
make doctor-fix-dry                # plan, no mutations
make doctor-fix-apply ARGS="--yes" # applies safelisted repairs (see §5 gates)
```

---

## 4. Cluster A — Doctor hardening (LUM-337/340/341/343/344/494)

Shell CLI under `scripts/doctor/` + contract tests in `orchestrator/tests/test_doctor_cli.py`.

**What shipped:**
- **LUM-494** — restart-loop guard: `count_recent_restarts()` reads applied `compose_restart_service`
  rows from the repair audit NDJSON; refuses a further restart for the same service once
  `LUMOGIS_DOCTOR_RESTART_LOOP_MAX` (default 3) is hit within `…_WINDOW_SEC` (default 3600). Skipped
  repairs aren't audited (don't advance the counter). Guard tolerates malformed audit rows.
- **LUM-340** — core-service allowlist (K) externalised to versioned `scripts/doctor/core-services.json`
  (override → script-local → repo-relative → built-in fallback; malformed manifest can't widen K).
  **No behaviour change** (set unchanged).
- **LUM-341** — `.env` config-edit safelist (`set_env_key` repair kind). **Deny-by-default + opt-in**:
  off unless `LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1` *and* `--fix --apply`. Append-only (refuses existing/
  commented/secret-shaped/non-safelisted keys), value resolved from the manifest (never from the
  stream), shell-safe value charset (`ENV_VALUE_RE`), atomic temp+rename, `0600 .env.bak-<ts>` rollback.
  Safelist in `scripts/doctor/env-safelist.json`. ADR-065 amendment documents the 7-hazard threat model.
- **LUM-337** — `jq` added to the orchestrator image (doctor `--json` needs it; CI runs the contract in-container).
- **LUM-343** — Makefile sugar: `doctor-fix` / `doctor-fix-dry` / `doctor-fix-apply` (no new behaviour/contract).
- **LUM-344** — backfilled LUM-320 §Test cases.

**Files:** `scripts/doctor/{repair.sh,checks/config.sh,core-services.json,env-safelist.json,schema.v2.json,README.md}`,
`orchestrator/Dockerfile`, `Makefile`, `orchestrator/tests/test_doctor_cli.py`,
`docs/decisions/{061,065}-*.md`.

**Verify-plan:** doctor suite **71 passed, 4 skipped** (run `make compose-test-doctor`). On a live Compose
stack, exercise `make doctor-fix-dry` (no mutation) then `make doctor-fix-apply ARGS="--yes"` with
`LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1` to see the `.env` safelist + restart-loop guard in action. The one
explicitly-deferred test case (ollama-pull timeout) is documented in `test_doctor_cli.py` as manual/integration.

**Merge notes:** self-contained under `scripts/doctor/` + `orchestrator/tests/`; no shared files with Cluster B.

---

## 5. New configuration knobs (all opt-in, safe defaults)

| Env var | Default | Effect |
|---|---|---|
| `LUMOGIS_DOCTOR_ALLOW_ENV_EDITS` | unset (off) | Required (with `--fix --apply`) to enable `set_env_key` `.env` edits |
| `LUMOGIS_DOCTOR_RESTART_LOOP_MAX` | `3` | Restart-loop guard threshold (`0` disables) |
| `LUMOGIS_DOCTOR_RESTART_LOOP_WINDOW_SEC` | `3600` | Guard lookback window (`0` = full history) |
| `LUMOGIS_DOCTOR_CORE_SERVICES_FILE` | unset | Override path for the core-service (K) manifest |

No new env vars for the Web cluster. `GET /api/v1/health` reuses existing auth.

---

## 6. Cluster B — Web UX (LUM-420/212/211/512)

Full detail in `docs/handover/2026-06-19-web-ux-cluster-LUM-212-211-512.md`. Summary:

- **LUM-212** — shared `Skeleton`/`LoadingPlaceholder` primitive (reduced-motion aware, a11y `role=status`);
  wired into document library, audit log, chat typing indicator, entity detail.
- **LUM-211** — `ErrorState` (never raw 500s; always an action; `lumogis doctor` hint) + app-level
  `AppErrorBoundary` (outer = shell/nav net; inner = keeps nav alive on a page crash).
- **LUM-512** — new non-admin `GET /api/v1/health` + `useServiceHealth` poll + `ServiceDegradationBanner`
  in chat (Ollama→hard-fail alert; Qdrant→degraded "KG-only"; graph→degraded). Request is source of
  truth: chat errors call `refreshHealth()` to reconcile the banner immediately.
- **LUM-420** — Playwright mobile smoke for the chat sidebar collapse.

**Review trail (run on the final state `98b3c30`):**
- **Verify** — PASS on the live `GET /api/v1/health` (401→200, correct non-sensitive shape, `degraded`
  not 500 when stack down, TTL cache 174ms→5ms→5ms). Frontend GUI verification deferred to on-stack e2e.
- **Code review** — 10 findings, **all fixed** in `98b3c30`.
- **Security review** — **no HIGH/MEDIUM** (auth gate correct; whitelisted non-sensitive projection;
  process-global cache holds only stack-wide data; no injection; React = no XSS).

**⏳ Live-stack gaps (Cluster B):**
1. Full orchestrator suite in-container (`make compose-test`) — local venv lacks `qdrant_client`/`argon2`.
2. Web e2e (`chat-degradation`, `chat-sidebar-mobile`) — needs smoke creds + stack (§3).
3. **Real kill-a-service drive** — stop Ollama/Qdrant/FalkorDB and confirm `/api/v1/health` flips the
   service to `down`, the banner appears, and a failed chat reconciles it immediately. (Stubbed by the e2e.)

### New API contract — `GET /api/v1/health`
- Auth: `require_user` (any authed user; 401 unauth). Non-admin by design.
- Response: `{ "overall": "ok|degraded|down", "services": { "ollama|qdrant|graph": "<state>" } }`.
- **Whitelisted to `{ollama,qdrant,graph}`** (full topology + runtime detail withheld from non-admins).
- Cache: process-global, serve-stale-while-revalidate, ~10s TTL (stack-wide data, no per-user content).
- OpenAPI snapshot committed; generated TS types are gitignored → `npm run codegen`.

---

## 7. Design / recon outcomes (no code — captured for follow-up)

### LUM-511 — ingest/transcription progress — **DEFERRED** (documented on the Linear ticket)
ACs are blocked on backend features that don't exist: doc status is only `indexing|indexed|failed`
(no per-stage data); transcription is **synchronous** (nothing to poll); upload is **single-file**
(no batch to count). Recommended build order is on the ticket. No code shipped — deliberately did not
fabricate UI for absent backend flows.

### LUM-159 — BGE reranker UI toggle — **EXPLORED, ready to plan**
- **Backend already exists on `dev`** (Compose): `GET/PUT /settings` handle `reranker_enabled`
  (writes `RERANKER_BACKEND=bge|none` to `/project/.env` + settings store); `POST /settings/restart`
  → stack-control `compose up --force-recreate orchestrator`. `config.get_reranker()` is a cached
  singleton → **restart-required is correct**. BGE = `BAAI/bge-reranker-base`, ~400 MB, ~1.36 GB peak RAM.
  The legacy `dashboard/index.html` has the full toggle+restart+poll flow.
- **The LUM-159 work is a frontend port** into Lumogis Web admin: a `src/api/adminSettings.ts` client,
  the toggle + ported RAM/precision copy, a restart-and-wait UX, and a "reranker active (~1.36 GB)" chip
  in `AdminSystemStatusView`. vitest-verifiable.
- **Real open issue (bigger than the blocker):** the **Lumogis Server (native) restart path is undefined**
  — `/settings/restart` is Compose-only (stack-control sidecar). Scope 159 to: full flow on Compose;
  on native server, persist + show manual-restart guidance until LUM-466's server supervisor lands.
  Gate the auto-restart button on stack-control reachability.
- **Small backend delta:** `GET /settings` returns the *pending/desired* `reranker_enabled`, with no
  field exposing the *live* `RERANKER_BACKEND` → add it for an honest "change pending — restart to apply" chip.

### LUM-159 ↔ LUM-462 relationship
LUM-462 = generic optional-capability "add-back" framework + cost/benefit settings UI (**not built**).
The reranker was coupled to it in early planning, then **decoupled by ADR-093** ("no Class-A/B split;
torch is present in the server") — the BGE reranker is **built-in, not an optional capability**, and its
toggle is a plain admin setting that already ships. So **the Linear `blocks` edge (462→159) is stale for
the reranker**; 159 can proceed independently. Only overlap: 462 will later generalize the cost/benefit
settings UI, which 159's toggle can be refactored into. **Recommendation: clear the stale edge in Linear,
then plan 159.**

---

## 8. Per-ticket status + merge readiness

| Ticket | Linear state | Code on branch | Verify-plan | Merge-ready? |
|---|---|---|---|---|
| LUM-337 | — | ✅ | jq in image; doctor `--json` works | ✅ (in-container test) |
| LUM-340 | — | ✅ | 4 manifest tests | ✅ |
| LUM-341 | — | ✅ | safelist tests; suite 71/4 | ✅ (live: exercise `--fix --apply`) |
| LUM-343 | — | ✅ | sugar only | ✅ |
| LUM-344 | — | ✅ | backfilled cases | ✅ |
| LUM-494 | — | ✅ | guard tests | ✅ |
| LUM-420 | In Progress | ✅ spec | on-stack e2e | after e2e run |
| LUM-212 | In Progress | ✅ | vitest 321 | ✅ FE; merge w/ cluster |
| LUM-211 | In Progress | ✅ | vitest 321 | ✅ FE; merge w/ cluster |
| LUM-512 | In Progress | ✅ | vitest + pytest 23 + live drive | ✅ after on-stack kill-service check |
| LUM-511 | Backlog | ❌ deferred | n/a | n/a |
| LUM-159 | Backlog | ❌ explored | n/a | plan next (clear 462 edge) |

(Children **LUM-511** and **LUM-512** were created this session under LUM-212 / LUM-211 respectively.)

---

## 9. Merge guidance

1. **Run the live-stack checks** (§3 container suite + e2e; §6 kill-service drive; §4 doctor `--fix` drive).
2. **Scope:** the two clusters are file-disjoint (Cluster A under `scripts/doctor/` + `orchestrator/tests/`
   + `Dockerfile`/`Makefile`; Cluster B under `clients/lumogis-web/` + `orchestrator/{routes/api_v1,services,models}`).
   You can merge the whole branch to `dev`, or split by cluster — both are clean.
3. No migrations, no new env vars required at runtime, no new deps. The doctor env knobs (§5) default off.
4. After merge: deferred LUM-512 items (cloud fallback/send-disable, session-expired/slow-op, server-side
   retrieval fallback, ops kill-service e2e), **LUM-511**, and **LUM-159** remain tracked in Linear.

---

## 10. Verification evidence already captured this session
- vitest **321 passed (56 files)**; `tsc -b` clean; eslint clean.
- orchestrator pytest **23 passed** (health + stack_status + admin stack-status + openapi snapshot).
- doctor suite **71 passed, 4 skipped** (as of `3bdb404`).
- Live in-process drive of `GET /api/v1/health` (401→200, non-sensitive shape, graceful-when-down, TTL cache).
- Code review: 10 findings fixed. Security review: no HIGH/MEDIUM.
