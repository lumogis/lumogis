# ADR-223: Lumogis Web dependency advisories — scoped overrides, one time-boxed allowlist entry, ESLint 10 (LUM-859)
**Status:** Finalised (canonical copy: `docs/decisions/223-lum-859-web-deps-security-audit.md`)
**Created:** 2026-11-06
**Last updated:** 2026-11-06 (finalised by /verify-plan --headless)
**Decided by:** claude-opus-5-5 (`/explore --headless`, run-workflow LUM-859); finalised by claude-fable-5-1 (`/verify-plan --headless LUM-859`)
**Linear:** [LUM-859](https://linear.app/lumogis/issue/LUM-859/ci-security-audit-is-red-the-remaining-npm-advisories-need-major)

## Context

The blocking `security-audit` CI job (ADR-060) has been red on `dev` since at least 2026-08-20. `npm audit` on `clients/lumogis-web` reports 15 advisories. They come from four root advisories, all in dev or build tooling:

- **js-yaml GHSA-2883-xcg3-v3hh (high):** fixed in 4.3.2, but blocked by our own exact-pin override `"js-yaml": "4.3.1"` (added 2026-08-07 to pass an earlier audit).
- **minimatch GHSA-g3hv-75wg-m249:** fixed only in minimatch 11. `typescript-eslint@8.72.1`, `eslint@10.14.0` and `@eslint/config-array` (all latest) declare `^10`.
- **glob GHSA-3g3m-2fjr-6pw4:** fixed only in glob 14.0.2. `workbox-build@7.4.1` (latest) declares `^11`.
- **deepmerge GHSA-gcqx-w48m-w2h4 (low):** **no fixed release exists.** It is reached via `vite-plugin-pwa` → `workbox-build` → `@rollup/plugin-node-resolve`.

The job also has a second, hidden failure. `scripts/audit_local.sh` runs under `set -euo pipefail`, so the failing npm half exited the script before the pip-audit half ever ran in CI. Measured during planning, the pip half is red as well, on one private build-host tool pin that has an upstream advisory. Fixing npm alone would leave the job red.

ESLint 8 is end-of-life, and so is ESLint 9 (2026-08-06). The toolchain also runs on Node 20, which reached end-of-life on 2026-04-30, and current majors of vitest, jsdom, jest-dom and axe-core require Node 22. Scope forbids `npm audit fix --force`, which proposes downgrades.

## Decision

Clear every advisory that has a published fix with **narrowly scoped, caret-ranged npm `overrides`**: `js-yaml ^4.3.2`, `workbox-build › glob ^14.0.3`, `@typescript-eslint/typescript-estree › minimatch ^11.0.5`, `eslint › minimatch` and `@eslint/config-array › minimatch ^11.0.5`. Never use top-level `glob`/`minimatch` overrides and never use exact pins. Suppress **only** deepmerge GHSA-gcqx-w48m-w2h4, through a fail-closed, table-driven allowlist in `scripts/npm_audit_allowlist.py` (stdlib Python with unit tests, called from `scripts/audit_local.sh`, which replaces the inline heredoc). The entry passes only if all seven conditions hold: the advisory is the only one on the package; at least one instance is locked and every locked version is ≤ the gated version; every instance is dev-only; it has no dependents outside an explicit set (including the root package); no import from `src/` (the reachability check carried over from the old react-router entry); the **review-by date** has not passed; and only listed parents with no other advisory are popped with it. The dormant react-router entry is removed. Bump the private build-host tool pin that fails pip-audit, and keep any public-shipping text from naming it. Add a non-empty-precache assertion to `check-pwa-dist.mjs`, because an override mismatch in the workbox glob chain fails silently as an empty manifest. Migrate to **ESLint 10** flat config with rule parity: react-hooks 7 configured as the 5.x-equivalent rules, and the new `no-useless-assignment` hits fixed rather than disabled. Refresh the rest of the toolchain only within what Node 20 and current peer ranges allow (TypeScript ≤ 5.9, Vite 6.4.x, vitest 4.x, jsdom 25). Land each bump as its own reviewed commit, in two tranches: the gate fix (precache guardrail, allowlist, overrides, pin bump) is mergeable on its own, and the toolchain refresh (ESLint 10, in-range refresh, TypeScript 5.9, Playwright 1.65) follows it. ESLint rule parity is defined against the measured `recommended` diff: four rules were removed upstream, and `no-inner-declarations` is re-added for parity.

## Alternatives Considered

Full detail: `.cursor/explorations/LUM-859-web-deps-security-audit.md`.

- **Allowlist glob, minimatch and deepmerge; bump only js-yaml:** rejected. It suppresses two advisories whose fixes install and work today (spike-proven).
- **Wait for upstream releases:** rejected as the primary path. The gate stays red indefinitely and deepmerge is never fixed. Upstream releases are the removal condition for each override instead.
- **Alias-override or `patch-package` deepmerge:** rejected. The fork API is incompatible, or it adds a postinstall hook to `npm ci` (ADR-043), and both add supply-chain surface for a low-severity build-time advisory.
- **`npm audit --omit=dev` / `--audit-level`:** rejected. It is a blanket ignore of build tooling that runs in CI and in the image build, which contradicts ADR-060.
- **ESLint 9:** rejected. It is already end-of-life, so it would mean a second migration.
- **Vite 8, React 19, TypeScript 6/7, vitest 5, jsdom 30+, Node 22 baseline inside this change:** deferred to separate issues. They change the shipped bundle or runtime behaviour, are blocked by peer ranges (TypeScript), or need the Node baseline moved first.

## Consequences

- **Easier:** the audit gate is green and stays meaningful, since everything fixable is fixed in the installed tree. The allowlist cannot go dormant unnoticed because entries expire. An empty PWA precache becomes a red build instead of a silent offline-shell regression. The lint stack is on a supported ESLint.
- **Planned red:** on the review-by date (2027-02-04), every `dev` push and every `make doctor` fails `security-audit` until someone re-checks deepmerge upstream and edits the table. That is the design. A dated Linear reminder (about one week earlier) makes it a planned event rather than a surprise.
- **Harder:** `overrides` must be maintained and removed when typescript-eslint (#13145), ESLint (#21463) and workbox (#3599) ship fixes. Changing `overrides` does not re-resolve existing nested lockfile entries, so verify with `npm ls`, not by reading `package.json`. Cross-major overrides run dependencies outside their declared ranges, which is why only scoped overrides plus an output-equivalence check (byte-identical `dist/sw.js`) are acceptable.
- **Future chunks must know:** TypeScript is capped at 5.9 by `typescript-eslint` (<6.1), `openapi-typescript` (^5) and the TS-compiler-API test from LUM-852. The Node-baseline move gates vitest 5, jsdom 31, jest-dom 7, axe-core 4.14 and react-router 8. No CI workflow runs web lint or unit tests today; only `make verify-public-rc` does.

## Revisit conditions

- `@typescript-eslint/typescript-estree` declares `minimatch ^11`: drop that override.
- `eslint` / `@eslint/config-array` declare `minimatch ^11`: drop those overrides.
- `workbox-build` releases with `glob ^14.0.2`: drop the glob override.
- A fixed `deepmerge` (>4.3.1) is published, or `@rollup/plugin-node-resolve` drops deepmerge: remove the allowlist entry. Re-assess at the entry's review-by date in any case.
- npm stops resolving the scoped overrides (behaviour change in npm's override semantics), or `dist/sw.js` precache contents differ from the pre-override build: fall back to the allowlist for that package (Option 2) and record why.
- The web toolchain moves off Node 20: revisit the vitest, jsdom, jest-dom, axe-core and `@types/node` caps.

## Implementation notes (as shipped, 2026-11-06)

- The allowlist table is `ALLOWLIST` in `scripts/npm_audit_allowlist.py` (one entry: deepmerge ≤ 4.3.1, `review_by` 2027-02-04), called from `apply_npm_audit_allowlist` in `scripts/audit_local.sh`; the heredoc and the dormant react-router entry are gone. 30 subprocess tests in `orchestrator/tests/test_npm_audit_allowlist_script.py` cover every condition, the expiry boundary, the transitive chain pop, the `src/` import scan and the `--today` test hook.
- `package.json` `overrides` holds exactly the five scoped caret entries from the Decision. Resolved tree (`npm ls`): one `glob@14.0.4` under `workbox-build`, `minimatch@11.0.5` under `eslint`, `@eslint/config-array` and `@typescript-eslint/typescript-estree`, `js-yaml@4.3.2`; `filelist` and `@redocly/openapi-core` keep their nested `minimatch@5.1.9`; `glob@7` disappeared with ESLint 8's `flat-cache@3`, as the plan allowed. `npm audit` reports only the four deepmerge-chain names.
- `check-pwa-dist.mjs` takes an optional dist directory and fails on an un-injected or empty precache; six Vitest cases in `tests/pwa/checkPwaDist.test.ts`.
- ESLint 10 flat config (`eslint.config.js`) with `defineConfig`; measured parity against the ESLint 8 `--print-config` dump: only `no-extra-semi` and `no-mixed-spaces-and-tabs` dropped (prettier covers both), `no-inner-declarations` re-added at `error` with `blockScopedFunctions: "disallow"`, six rules added by ESLint 10 `recommended`, no typescript-eslint rule changed; the linted-file set is the ESLint 8 set plus the one new test file.
- Toolchain: TypeScript 5.9.3 (one typing-only fix, `Uint8Array<ArrayBuffer>` in `webPushBrowser.ts`), `vite-plugin-pwa` 2.0.0, Vite 6.4.5, Playwright 1.65.0, in-range minors; `engines.node >= 20.19.0`. The precache keeps 10 URLs and the same non-hashed set; only the hashed bundle name changed.
- The build-host tool pin was bumped and its lock-compile flags smoke-tested into `/tmp` (exit 0, `--find-links` line present; the committed locks untouched).
- Deviation recorded by `/verify-plan`: the eight planned per-step commits landed as one commit on `agent/lum-859` (the headless `/implement` step does not commit); the per-step gate artefacts exist under `/tmp/lum859-*` on the implementing host only. The Playwright 1.65 lane was not run on the implementing host (long-lived dev stack) — tracked as a P1 `gate:pre-main` child.

## Status history
- 2026-11-06: Draft created by /explore (headless, run-workflow)
- 2026-11-06: Revised during /review-plan --arbitrate R1. The allowlist moved to a tested table in `scripts/npm_audit_allowlist.py`, with a `src/` import condition and fail-closed empty-lock and root-dependent handling. The pip-half masking and the build-host pin bump were added to Context and Decision. The expiry's planned-red consequence and the two-tranche landing were added. The ESLint parity definition now follows the measured upstream `recommended` diff. Reason: critique R1 (D2 F1/F2, D5 F1, D1 F1, D8 F3).
- 2026-11-06: Finalised by /verify-plan — implementation confirmed decision (one process deviation: single commit instead of eight; Playwright lane deferred to a gate:pre-main child). Finalised copy at docs/decisions/223-lum-859-web-deps-security-audit.md
- 2026-11-10: public-boundary wording (LUM-898).
