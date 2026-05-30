# ADR-072: Client-only overlay distribution for Persona B (LUM-398)

**Status:** Finalised
**Created:** 2026-05-29
**Last updated:** 2026-05-29
**Decided by:** `/explore --headless LUM-398` → `/create-plan` → `/review-plan` (self + critique + arbitrate) → implement → `/verify-plan`
**Implementation:** `agent/lum-398` worktree (verify-plan 2026-05-29)

## Context

Persona B (household member) needs a thin overlay installer that points at an existing household Lumogis server — no local Core, Qdrant, or Ollama. LUM-329 (ADR-069) and LUM-397 (ADR-071) already deliver the thin HTTP client with keychain JWT auth and role gating. LUM-398 adds **distribution profile + first-run UX** and fixes search being incorrectly gated on empty `libraryRoots`.

Exploration and plan: `.cursor/explorations/LUM-398-client-only-overlay.md`, `.cursor/plans/LUM-398-client-only-overlay.plan.md`.

## Decision

### Distribution profile (A2)

- **Tauri 2 `--config` merge** via `tauri.client-only.conf.json` (RFC 7396) on the **same** `src-tauri/` crate — no fork.
- **CI** (`.github/workflows/desktop-build.yml`) passes `--config src-tauri/tauri.client-only.conf.json` to `tauri-action@v0` (`projectPath: clients/lumogis-desktop`); upload artefacts **`lumogis-overlay-{platform}-{arch}`**.
- **Local parity:** `make desktop-build-client-only`.
- **Identifier:** keep `com.lumogis.overlay` (side-by-side install deferred to LUM-396 if needed).

### First-run onboarding (B1)

- **In-webview wizard** in `ui/main.ts` / `ui/overlayUi.ts`: server URL → `probe_server_health` (`GET /healthz`) → `probe_auth_state` (optional URL, no persist) → `auth_login` (optional URL) → `complete_onboarding` (single persist).
- **`overlay.json` schema v2** with `onboardingComplete`; loader accepts v1; pure **`onboarding_complete_for`** migration after `AppState` exists (roots, session, non-default URL).

### Search without local roots

- **`isSearchDisabled`:** only `needsLogin()` or `authMode === "unreachable"` — not empty `libraryRoots`.
- **Open/Reveal:** blocked in UI when roots empty with explicit alert; empty `hit.id` rows remain disabled (“Path unavailable”).

### Security / roles (unchanged protocol)

- No orchestrator changes; sessions keychain-only; `canManageIngestPaths` / `canUploadIngest` helpers tested in Vitest.

## Consequences

- **Easier:** Persona B download → URL → login → search without Docker; documented seam for LUM-396 `tauri.bundled.conf.json`.
- **Harder:** LUM-396 must adopt this profile model; LUM-402 should add Playwright for onboarding (deferred).
- **Behaviour change:** admins with empty roots can search (open still requires roots).

## Revisit conditions

- LUM-396 bundled Rust divergence may need cargo `bundled` feature alongside config overlay.
- Multi-step onboarding may warrant a dedicated window (rejected for v1).
- Code signing / notarisation for `lumogis-overlay-*` — child of LUM-329 (operator follow-up).

## Status history

- 2026-05-29: Draft created by `/explore --headless LUM-398`.
- 2026-05-29: Finalised by `/verify-plan` — implementation confirmed decision.
