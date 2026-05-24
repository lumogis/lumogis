# ADR 037 — GHCR publishes only from exported public repository (trusted source boundary)

**Status:** Finalised  
**Date:** 2026-05-13  
**Issue:** [LUM-225](https://linear.app/lumogis/issue/LUM-225/migrate-publish-imageyml-to-public-repo-images-must-only-build-from)  
**Related:** ADR 036 (GHCR multi-arch + compose overlay — LUM-192); LUM-192 (publish pipeline origin)

## Context

Private **`lumogis-app`** workflows could advance **`ghcr.io/lumogis/*:latest`** independently of **`/publish-private-main-to-public`**, **`CHANGELOG.md`**, **`docs/capabilities.md`**, and **`verify-public-rc*`** gates. Maintainer docs and skills assumed Makefile RC targets on private `main`, yet **`lumogis-app/Makefile`** lacked **`verify-public-rc`** / **`verify-public-rc-full`** (doc/skill drift).

**ADR 036** (LUM-192) established multi-arch GHCR publish and the `docker-compose.ghcr.yml` overlay. This ADR adds the **trusted source repo boundary** beside ADR 036: the same images must be produced only from the public AGPL tree, not from private development history.

## Decision

1. **`publish-image.yml` lives only in `lumogis/lumogis`** (public repo) — deleted from `lumogis/lumogis-app`. Triggered on public `main` push, semver `v*` tags, and `workflow_dispatch`. Guard: `github.repository == 'lumogis/lumogis'` prevents fork publishes.

2. **All third-party Action refs pinned to immutable commit SHAs** (not mutable `@main` or floating `@vN`) — supply-chain posture for a public publish workflow. SHA + version comment for human readability.

3. **`Makefile` targets `verify-public-rc` and `verify-public-rc-full`** added to `lumogis-app` as the local gate before `/publish-private-main-to-public`. Backed by existing `scripts/integration-public-rc.sh` (subcommand `full-cycle`) and `docker-compose.public-rc-stack.yml`. `check-public-export.sh` is called with explicit export path (`/tmp/lumogis-upstream-export`).

4. **Publish-private-main-to-public skill** updated: removes "or the Makefile's documented equivalent" caveat — targets are now real.

5. **Docs/skills/context-pack** updated to reflect that GHCR images originate from verified public source only.

## Alternatives Considered

See `.cursor/explorations/LUM-225-publish-image-public-verified-main.md`:
- **`repository_dispatch`-only bridging** (Option C) — dismissed: heavy IAM overhead vs benefit.
- **Third-party CI** (Option B) — rejected as default OSS posture.

## Consequences

- **Self-hosters:** `docker pull ghcr.io/lumogis/lumogis-orchestrator:latest` reflects a tree that passed the full public verification gate — not an intermediate private-main state.
- **Maintainers:** Must run `make verify-public-rc` before `/publish-private-main-to-public`; `web-codegen-check` (same as `make openapi-check`) runs **offline** via `dump_openapi` vs the committed snapshot — **no** `LUMOGIS_OPENAPI_URL`. Use **`VERIFY_PUBLIC_RC_SKIP_WEB_CODEGEN_CHECK=1`** only when the Makefile documents skipping that step; optional **live** codegen remains **`npm run codegen -- --live`**.
- **Dual-publisher window closed:** deletion of `publish-image.yml` from `lumogis-app` (disable → delete sequence) ensures no racing `latest` from two repos.
- **Supply chain:** Immutable SHA pins on all Actions in the public workflow.

## Implementation notes (verification)

| Topic | Evidence |
| --- | --- |
| `verify-public-rc` Makefile targets | `ca83054` — `feat(ci): verify-public-rc Makefile targets (LUM-225)` |
| Docs / context-pack / skill update | `1a695a6` — `docs(ci): verify-public-rc strategy + context pack update` |
| CHANGELOG 0.3.0 + capabilities | `f512285` — `docs: CHANGELOG 0.3.0 + capabilities GHCR entry (LUM-225)` |
| `publish-image.yml` in `lumogis/lumogis` | `533f248` — `ci: GHCR publish from verified public main only (LUM-225)` |
| 0.3.0 release sync to public | `547f44e` — `release: Lumogis 0.3.0` |
| Disable private publisher | `9ea5beb` — `ci: disable private GHCR publisher before deletion (LUM-225)` |
| Delete private publisher | `dc904fe` — `ci: remove private GHCR publisher — public repo only (LUM-225)` |
| `verify-public-rc` run | 1690 passed, 41 skipped / web-test 244 passed / integration full-cycle clean / export + check-public-export OK |
| `check-public-export.sh` Makefile fix | Path argument added — was defaulting to cwd |

## Relationship to ADR 036

ADR 036 answers: "which registry, which images, how is the compose overlay structured?"  
ADR 037 (this ADR) answers: "from which source repo must those images be built?"  
Both are required for the full trusted-publish story.

## Relationship to ADR 061 (LUM-303)

**[ADR 061 — LUM-303](061-lum-303-public-ci-parity-openapi-check-via-export.md)** adds an export-time **presence** contract so the public tree keeps the same **`.github/workflows/ci.yml`** `openapi-check` job surface and its offline inputs (Makefile, `dump_openapi.py`, web snapshot/codegen inputs, breaking-check fixtures) — enforced by **`scripts/check-public-export.sh`** during **`verify-public-rc`**, consistent with this ADR’s export-mediated public CI story (no second workflow source).

## Status history

- 2026-05-12: Draft created by `/explore` LUM-225 (`.cursor/adrs/lum_225_publish_image_public_verified_main.md`)
- 2026-05-13: Finalised by `/verify-plan` — implementation confirmed decision; `check-public-export.sh` Makefile fix recorded as deviation (unambiguous correction, not a plan deviation).
- 2026-05-23: Cross-reference added by `/verify-plan --headless` LUM-303 — **ADR 061** documents the OpenAPI CI export presence contract enforced in `check-public-export.sh` (see **Relationship to ADR 061**).
