# ADR 068: LUM-60 — e2e Postgres helper spawn without shell (as-shipped)

**Status:** Finalised
**Created:** 2026-05-27
**Last updated:** 2026-05-27
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-05-27 (Composer)
**Plan:** none — shipped via Cursor agent branch before formal plan / verify for this slice
**Exploration:** `.cursor/explorations/lum_60_e2e_postgres_spawn_no_shell_retro.md`
**Draft mirror:** `.cursor/adrs/lum_60_e2e_postgres_spawn_no_shell.md`
**Linear:** [LUM-60](https://linear.app/lumogis/issue/LUM-60/fp-047-optional-ci-workflow-make-web-e2e-web-e2e-prove-stack)

## Context

**LUM-60** shipped optional CI Playwright e2e (**ADR 064**). Host-side helper **`resetOnboardingCompletedAt`** in **`clients/lumogis-web/tests/e2e/e2e-postgres.ts`** used **`execSync`** with shell-interpolated command strings. Even with **`JSON.stringify(sql)`**, command substitution in **`LUMOGIS_WEB_SMOKE_EMAIL`** (e.g. **`$(echo …)`**) could execute in the shell before **`psql`** ran.

Fix landed on **`dev`** via **`cursor/critical-bug-investigation-9fd5`** (commit **`edc7ab398`**, cherry-pick **`93bd5c30a`**, 2026-05-27) without a Product OS plan/verify loop. Scope is **test/e2e infrastructure**, not production runtime.

## Decision

1. Replace **`execSync(command: string)`** with **`spawnSync(executable, argv[])`** for host **`psql`** and **`docker compose exec … psql`** fallback paths — **no shell**.
2. Pass SQL as a single **`-c`** argv element; smoke email SQL-escaped with **`'`** doubling only.
3. Export optional **`deps.spawnSync`** for Vitest injection.
4. Add **`clients/lumogis-web/tests/e2e-postgres-spawn.test.ts`** asserting command substitution stays literal and **`shell`** is not enabled.

## Alternatives considered

- **Sanitize smoke email only** — rejected; defense-in-depth requires no shell for untrusted-shaped strings.
- **Parameterized psql `-v`** — not adopted; argv **`-c`** with escaping is sufficient for test-only reset.
- **Playwright-only fix** — rejected; helper is shared by specs and CI bootstrap paths.

## Consequences

**Positive:** E2e onboarding reset cannot execute arbitrary shell via crafted smoke email.

**Limits:** Other test **`execSync`** call sites not audited in this slice; SQL string building remains (test-only).

## Revisit conditions

- New host subprocess helpers in **`clients/lumogis-web/tests/`** — use argv **`spawnSync`**, not shell **`execSync`**.
- Broader test-harness security audit requested — grep e2e tree for **`execSync`**.

## Linear linkage (Product OS)

- **LUM-60:** parent web e2e CI programme — post-ship helper hardening (comment via **`/linear-update`**, not **LUM-323** scope).
- **New issue needed:** no

## Testing retrospective

**`npm test -- tests/e2e-postgres-spawn.test.ts`** (lumogis-web) — **1 passed** on merged **`dev`**. Full Playwright e2e suite not re-run for this retro slice.

## Status history

- **2026-05-27:** Finalised by **`/record-retro`** — cherry-pick **`93bd5c30a`** on **`dev`**.
