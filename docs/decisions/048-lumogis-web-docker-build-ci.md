# ADR-048: lumogis-web Docker build CI job (LUM-254)

**Status:** Finalised
**Created:** 2026-05-16
**Finalised:** 2026-05-16 — `/verify-plan --headless` (implementation confirmed)

## Context

Post-LUM-224, `clients/lumogis-web/Dockerfile` enforces `npm ci` + lockfile + codegen + Vite build + nginx static-serve layer. `.github/workflows/ci.yml` ran Python lint/test and compose-policy checks but did not invoke `docker compose build` for the web image. Regressions surfaced only at `make verify-public-rc` or public GHCR publish.

Constraints:

- Must not push to any registry ([ADR 037](037-ghcr-publish-public-repo-only.md)).
- Must not break docs-only PRs with a pending required check ([LUM-258](https://linear.app/lumogis/issue/LUM-258) posture, sibling of LUM-193).
- Workflow token least privilege; fork PRs that match the path contract run untrusted build code on hosted runners.

## Decision

Add job **`web-docker-build`** to `.github/workflows/ci.yml` that runs **`docker compose build lumogis-web`** on `pull_request` (targets `main` / `master`) and on **`push`** to `main` and `master`, with **always-reporting** behaviour: the job always completes; path detection (`.github/scripts/web-docker-build-paths.sh`) sets `should_build` so irrelevant PRs skip the cold build but still log **`SKIP_WEB_DOCKER_BUILD: no paths matched contract`**. No registry login, no push, no Dockerfile edits in this chunk. Expose **`make web-docker-build`** for local parity.

## Alternatives considered

- **Dedicated workflow + build-push-action + cache** — deferred until cold-build p50 pain is measured.
- **`make verify-public-rc` on every PR** — rejected (scope explosion).

## Consequences

**Positive:** PR-time catch of `npm ci` / codegen / Vite / nginx layer failures; same command locally and in CI.

**Operational:** Branch protection should mark **`web-docker-build`** required only after a green rehearsal on `main` and a docs-only skip proof; operators reconcile fork-workflow approval settings with org risk tolerance.

**Trade-off:** Orchestrator-only PRs can still merge without cold-building the web image if `openapi.snapshot.json` drifts — `verify-public-rc` / `web-codegen-check` remain authoritative for that class of drift (documented in plan).

## Status history

- 2026-05-16: Draft in `.cursor/adrs/lum_254_web_docker_build_ci.md` from `/explore` + `/review-plan` R1.
- 2026-05-16: Finalised in-repo — CI job + path script + Makefile target shipped; canonical copy this file.
