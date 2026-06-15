# ADR 043 — lumogis-web Dockerfile: `npm ci` with copied `package-lock.json`

**Status:** Finalised (amended 2026-06-08 — LUM-253 BuildKit npm cache mount)
**Date:** 2026-05-15 / verified 2026-05-15; amended 2026-06-08
**Issue:** [LUM-224](https://linear.app/lumogis/issue/LUM-224/lumogis-web-dockerfile-switch-to-npm-ci-copy-package-lockjson)
**Related:** LUM-223 (OpenAPI codegen before `tsc`); [ADR 036](036-docker-image-ci-ghcr.md); [ADR 037](037-ghcr-publish-public-repo-only.md)

## Context

The `clients/lumogis-web` image previously ran `npm install` without copying the committed `package-lock.json`, so image dependency resolution could drift from the lockfile contributors and `audit_local.sh` treat as authoritative. **`scripts/codegen.mjs`** used `npx --yes`, which could silently fetch tooling outside the lockfile tree when `node_modules` was inconsistent.

## Decision

1. **Install:** `COPY package.json package-lock.json ./` then `RUN npm ci --no-audit --no-fund` in the image build stage.

2. **Codegen ordering (LUM-223):** After copying `openapi.snapshot.json`, `scripts/codegen.mjs`, configs, `public/`, and `src/`, run `npm run codegen` before `npm run build`.

3. **`.dockerignore`:** Exclude `src/api/generated`, `node_modules`, `dist`, VCS/dotenv/test artefacts, and other listed paths so build context stays small and secrets are not tarred into the daemon.

4. **Codegen spawn:** Invoke `npx` **without** `--yes` so missing `openapi-typescript` fails fast against the lockfile-installed tree.

5. **Regression guard:** Root `Makefile` target **`make web-dockerfile-check`** fails unless the Dockerfile contains both the lockfile `COPY` and a `RUN npm ci` line.

6. **BuildKit npm cache (LUM-253):** Declare `# syntax=docker/dockerfile:1` and use `RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund` so repeated image builds reuse npm download cache; lockfile reproducibility unchanged (`docker build --no-cache` still runs full `npm ci`).

## Alternatives (deferred)

- `pnpm` / Yarn Berry — rejected for this slice.

## Consequences

- Image dependency graph matches the committed lockfile; lockfile drift fails `npm ci` before network-heavy ambiguity.
- Contributors must update `package.json` and `package-lock.json` together when adding dependencies.
- Draft mirror: `.cursor/adrs/lum_224_lumogis_web_dockerfile_npm_ci.md`.

## Implementation notes (verification)

- `docker build --no-cache -f clients/lumogis-web/Dockerfile clients/lumogis-web` — exit 0; log shows `npm ci`, `npm run codegen`, `vite build`.
- `make web-dockerfile-check` — exit 0.
- BuildKit cache mount speeds warm rebuilds; poisoned cache → `docker builder prune` or `docker build --no-cache` (see `docs/private/ops/dev-cheatsheet.md`).
