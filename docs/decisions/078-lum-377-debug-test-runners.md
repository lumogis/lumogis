# ADR-078: Test audit and debug test runners (LUM-377)

**Status:** Finalised
**Created:** 2026-06-03
**Last updated:** 2026-06-03
**Decided by:** /explore; implementation confirmed by /verify-plan (2026-06-03)

## Context

Lumogis has many test/lint/gate entry points spread across the Makefile, RC
scripts, and CI workflows. Contributors and agents lacked a single inventory of
what runs when, and verbose test output burned context. LUM-377 adds thin
`scripts/debug/` wrappers (summary + tee-to-log) and documents release-stage
gating without replacing `make verify-public-rc(-full)`.

## Decision

- Keep **Makefile** and **`verify-public-rc(-full)`** as authoritative executors.
- Add **`scripts/debug/`** stage wrappers (`unit`, `lint`, `web`, `rust`,
  `integration`) with **`cli.sh list`**, **`make debug`**, **`make test-list`**.
- Canonical machine-readable inventory: **`scripts/debug/inventory.tsv`**;
  human prose: **`docs/testing/automated-test-strategy.md`**.
- **`pytest-agent-digest`** in **`orchestrator/requirements-dev.txt`** only;
  **`unit.sh`** sets **`PYTEST_ADDOPTS`** per invocation (CI unchanged).
- Heavy/destructive suites require **`--heavy`** or **`LUMOGIS_DEBUG_HEAVY=1`**.
- Defer **`registry.yaml`** + Makefile drift check until **LUM-384** needs it.

## Consequences

- Easier local/agent DX; LUM-384 can consume the inventory table.
- Another maintenance surface; inventory must stay aligned with Makefile over time.

## Status history

- 2026-06-03: Draft created by /explore (LUM-377)
- 2026-06-03: Finalised by /verify-plan — implementation confirmed decision
