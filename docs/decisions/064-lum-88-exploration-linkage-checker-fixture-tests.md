# ADR-064: Fixture tests for exploration ↔ Linear linkage checker (devtools)

**Status:** Finalised
**Created:** 2026-05-23
**Last updated:** 2026-05-23
**Decided by:** `/explore` + `/create-plan` LUM-88; finalised by `/verify-plan --headless` 2026-05-23

## Context

LUM-10 shipped `scripts/linear/check_exploration_linear_linkage.mjs` (read-only exploration ↔ Linear linkage checker) without a committed automated harness. Sibling Product OS scripts (`check_linear_evidence_index.mjs`, `product_os_reconcile.mjs`) already use `node:test` companions. LUM-88 closes that gap in **lumogis-devtools** only: subprocess black-box tests against `--json`, fixtures under `scripts/linear/__fixtures__/exploration-linkage/`, and registry rows so `check_exploration_linear_linkage.mjs --require-classified` exits **0** on a clean tree (including waivered legacy retros without YAML frontmatter).

## Decision

1. **Harness:** `scripts/linear/exploration_linear_linkage.test.mjs` using `node:test`, `spawnSync(process.execPath, [CHECK, ...args], { cwd: DEVTOOLS_ROOT })` (no `shell: true`), asserting on parsed `--json` (`findings`, `errors`, `warns`, per-finding `id` / `severity` / `path` where applicable).
2. **Fixtures:** Committed YAML under `scripts/linear/__fixtures__/exploration-linkage/` plus `mkdtempSync` under `os.tmpdir()` for ephemeral registry paths and `--exempt-baseline` smoke (including a short-lived probe file under `cursor/explorations/` removed in a `finally` block).
3. **Registry hygiene:** `cursor/reports/exploration-linear-grandfather-registry.yaml` gains **`active`** rows for **LUM-201** / **LUM-275** explorations and **`historical`** + **`waiver: true`** rows for six legacy `*_retro.md` files, matching the plan’s explicit path list.
4. **Discoverability:** `scripts/linear/README.md` § *Check exploration linkage* documents `node --test scripts/linear/exploration_linear_linkage.test.mjs` from the devtools repo root.

## Alternatives considered

- **Vitest** — rejected: unnecessary dev-dependency and runner split for stdlib-covered CLI tests.
- **Inline `--self-test` on the checker** — rejected: couples production script and test code; breaks LUM-86 / LUM-87 precedent.
- **Pytest in lumogis-app** — rejected: violates devtools-only scope and product/runtime boundaries.

(Full exploration: `.cursor/explorations/LUM-88-exploration-linkage-checker-tests.md`.)

## Consequences

- Regressions in registry parsing, severity mapping, `--require-classified`, or `--exempt-baseline` are caught by `node --test` before merge.
- **`node:test` remains the only harness** for `scripts/linear/*.test.mjs` in this family; do not introduce Vitest/Jest/Mocha for sibling scripts without a new ADR.
- Grandfather **`waiver: true`** rows remain the supported path for legacy retro markdown without YAML until `/record-retro` emits frontmatter (revisit in draft ADR conditions).

## Status history

- 2026-05-23: Draft created in `.cursor/adrs/LUM-88-exploration-linkage-checker-tests.md` via `/explore --headless` LUM-88.
- 2026-05-23: Finalised by `/verify-plan --headless` — implementation matches decision; canonical copy is this file (numbered **064** to avoid collision with **ADR-063** `063-lum-248-stack-control-pytest-parity-closure.md` on the Product OS line).
