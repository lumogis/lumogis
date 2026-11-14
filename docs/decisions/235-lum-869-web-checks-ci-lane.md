# ADR-235: Lumogis Web lint, unit tests, build and PWA dist check run on every `dev` push (LUM-869)
**Status:** Finalised
**Created:** 2026-11-07
**Last updated:** 2026-11-07
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-07 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/web_checks_ci_lane_retro.md`
**Draft mirror:** `.cursor/adrs/web_checks_ci_lane.md`
**Linear:** LUM-869

## Context

Only `web-docker-build` exercised the Lumogis Web tree on push, and it only proves the image builds. Nothing in CI ran `npm run lint`, `npm test` or `npm run verify:pwa-dist`, so an ESLint, vitest or precache-guardrail regression showed CI green. LUM-859 had to verify all three by hand.

## Decision

A separate `web-checks` job in `.github/workflows/ci.yml` runs, in order: `npm ci`, `npm run codegen` (from the committed OpenAPI snapshot), `npm run lint` (which carries `--max-warnings 0`), `npm test`, `npm run build`, `npm run verify:pwa-dist`, on Node 22.

- On `push` it always runs. On `pull_request`, `.github/scripts/web-checks-paths.sh` runs it when a changed path is under `clients/lumogis-web/`, or is `ci.yml` or the gate script.
- A skipped lane says so in the log (`SKIP_WEB_CHECKS`) and the job summary (LUM-788 convention).
- It is independent of `web-docker-build` and of `web-e2e.yml`.

## Alternatives considered

- **Add the steps to `web-docker-build`.** Not chosen: that lane needs Docker and Buildx, and a lint failure should not wait on an image build.
- **Reuse `web-docker-build-paths.sh`.** Not chosen: it ignores workflow-only changes and triggers on `docker-compose.yml`.
- **Path-gate pushes too.** Not chosen: the push gate is the one place a toolchain regression on `dev` would otherwise go unseen.

## Consequences

- Web toolchain regressions turn `dev` CI red.
- Each `dev` push runs `npm ci` and about 1,400 vitest tests on the self-hosted runner.

## Revisit conditions

- The runner runs short of `/tmp` or CPU (LUM-874): cache `node_modules` or gate pushes by path.
- The web toolchain moves off Node 22 (LUM-868).
- `web-docker-build` starts running lint or tests itself.

## Linear linkage (Product OS)

- **Existing issue:** LUM-869
- **New issue needed:** no
- **Historical evidence only:** no

## Testing retrospective

All six steps ran locally in the job's order and exited 0 (lint clean, 1,406 tests, precache 10 entries, PWA dist OK). The job has not run on a runner yet; closure needs one green `dev` CI run.

## Status history

- 2026-11-07: Finalised by /record-retro (retrospective).
