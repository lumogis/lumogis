# ADR-064: LUM-321 — make test pytest preflight + CONTRIBUTING venv one-liner

**Status:** Finalised
**Created:** 2026-05-23
**Last updated:** 2026-05-23
**Decided by:** /explore (Composer); confirmed by /verify-plan

## Context

Contributors who run `make test` without installing dev requirements first see low-signal Python import errors. **LUM-248** / **ADR 063** established that CI + documented venv paths already satisfy parity; the remaining gap is fail-fast UX and a copy-paste-friendly install command at the point of use.

Thomas locked **LUM-321** scope to two changes only: a Makefile pytest preflight before `make test`, and a CONTRIBUTING venv one-liner in **Running tests (local venv)** — not requirements-file taxonomy docs, not compose-target discoverability, not CI/runtime changes.

## Decision

Add a **`check-pytest`** Makefile phony target that verifies **`$(PYTHON)` can `import pytest`**, make **`test:` depend on it**, and print a stderr message pointing to **CONTRIBUTING.md — Running tests (local venv)** on failure (exit **2**, matching **`openapi-breaking-check`**). Add a **single chained `pip install` one-liner** in **CONTRIBUTING.md** § *Running tests (local venv)* for CI-equivalent local pytest setup, placed immediately before the existing **`make test`** / **`make lint`** block.

## Alternatives Considered

- **Inline preflight in `test:` only** — rejected as weaker than a named check target consistent with other Makefile guards.
- **`command -v pytest`** — rejected; mismatches `python -m pytest` / `PYTHON` override semantics.
- **Auto pip install on failure** — rejected; surprising and out of scope.
- **Do nothing** — rejected; explicitly deferred from LUM-248 to LUM-321.

Full comparison: `.cursor/explorations/archived/LUM-321-make-test-pytest-preflight-docs.md`

## Consequences

**Easier**
- First-time contributors get an actionable error before pytest stack traces.
- One copy-paste command for full local test deps at the point of use.

**Harder / unchanged**
- `requirements-test.txt` lighter-install confusion remains unless a future ticket addresses it.
- `lint` / `test-integration` still lack pytest preflight unless extended later.

**Future chunks must know**
- Host test entrypoint assumes **`check-pytest`** guard on **`test:`**; do not remove without ADR update.
- CI is unchanged — preflight is host-contributor UX only.

## Revisit conditions

- If Lumogis adopts a **`make setup-dev`** or **`uv sync`** standard that subsumes manual pip lines, revisit CONTRIBUTING one-liner and whether preflight should move to a shared **`check-dev-env`** target.
- If **`test-integration`** preflight is added, extend **`check-pytest`** reuse rather than duplicating inline checks.

## Status history

- 2026-05-23: Draft created by /explore
- 2026-05-23: Finalised by /verify-plan — implementation confirmed decision
