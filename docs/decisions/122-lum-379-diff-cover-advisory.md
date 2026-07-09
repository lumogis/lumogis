# ADR-122: diff-cover changed-line coverage — advisory rollout (LUM-379)

**Status:** Finalised

**Created:** 2026-06-22

**Last updated:** 2026-06-22

**Decided by:** as-shipped implementation (retrospective); `/evaluate` LUM-379 had recommended Defer — operator chose advisory first (Decision 1B)

**Finalised by:** /record-retro 2026-06-22

**Issue:** [LUM-379](https://linear.app/lumogis/issue/LUM-379)

**Related:** [ADR-079](079-lum-384-test-coverage-matrix.md); child **LUM-525** (required `--fail-under=80`)

## Context

LUM-379 adds OpenHuman-style changed-line coverage via diff-cover. `/evaluate` deferred required gate until LUM-384 + burn-in. Implementation landed **advisory** (no `--fail-under`, `continue-on-error` on PR step).

## Decision

- **`orchestrator/requirements-dev.txt`** — `pytest-cov`, `diff-cover`.
- **`.github/workflows/ci.yml`** — orchestrator + stack-control emit lcov; PR-only diff-cover report with exemptions (migrations, generated, vendor).
- **CONTRIBUTING.md** — advisory contract; required gate tracked on **LUM-525**.

## Consequences

- **Easier:** Visibility into uncovered changed lines without blocking merges yet.
- **Harder:** Required gate promotion needs exemption tuning + LUM-384 matrix.

## Status history

- 2026-06-22: Finalised by `/record-retro` — advisory rollout on `dev` @ `c1d30c1d4`.
