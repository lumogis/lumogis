# ADR-047: Compose policy adversarial CI fixtures (LUM-268)

**Status:** Finalised
**Created:** 2026-05-16
**Last updated:** 2026-05-16
**Linear:** [LUM-268](https://linear.app/lumogis/issue/LUM-268/ci-compose-policy-fails-docker-composetest-policy-adversarialyml) (parent policy programme [LUM-43](https://linear.app/lumogis/issue/LUM-43/fp-050-capability-composepolicy-guard-new-services-must-not-receive))

## Context

The `compose-policy` CI job runs `make compose-policy-check-baseline`, `make compose-policy-check`, `make compose-policy-check-adversarial`, and `make compose-policy-check-adversarial-envfile`. The two adversarial targets merge root `docker-compose.yml` with overlay files that must trigger **Pass A** of `scripts/check_compose_policy.py` (exit **1** — policy violation). The Makefile inverts exit **1** to Make success so the job proves the checker still catches regressions.

Those overlay filenames were listed in `.gitignore`, so the files never shipped; the checker exited **2** (missing file) and CI failed for the wrong reason.

## Decision

Ship two **tracked** root overlays — `docker-compose.test-policy-adversarial.yml` and `docker-compose.test-policy-adversarial-envfile.yml` — each defining a **synthetic non-allowlisted** service that violates Pass A (`POSTGRES_PASSWORD` in `environment` vs `env_file`). Narrow `.gitignore` to **local-only** patterns so scratch copies stay ignored while the canonical filenames remain in Git. **Do not** change the checker, allowlist, Makefile adversarial recipes, or the `compose-policy` workflow job in this chunk.

## Consequences

- Adversarial targets again prove **exit-code 1** from the checker (inverted to green Make) when policy is violated.
- Make recipes still assert **code** only, not **which** Pass A rule fired; violation-specific automation remains follow-up work under **LUM-43** (pytest on `check_compose_policy.py`).
- Fixtures are **parse-only** (no `image:` / `build:`); they are **not** for `docker compose up`. Headers warn operators and flag possible scanner noise on the literal key name `POSTGRES_PASSWORD` with a non-secret marker value in the public AGPL tree.

## Status history

- 2026-05-16: Draft rationale in `.cursor/adrs/LUM-268-compose-policy-adversarial-fixture.md` (exploration Option 1).
- 2026-05-16: Finalised by `/verify-plan --headless` — implementation confirmed; canonical copy this file (**047** chosen to avoid colliding with portfolio-reserved **`046-telemetry-md-zero-telemetry-proof.md`** naming on integration branches).
