# ADR 262 — Install from published images (LUM-980)

**Status:** Finalised (Passes 1–3 shipped; the first release that uses it is LUM-980 Pass 4)
**Created:** 2026-11-15
**Last updated:** 2026-11-15
**Decided by:** Thomas (install and update from two downloaded files, 2026-11-15); plan reviewed by self-review, Sonnet R1 and Fable R2 with two arbitration rounds
**Finalised by:** /verify-plan 2026-11-15 (Claude Code)
**Plan:** `.cursor/plans/LUM-980-install-from-published-images.plan.md`
**Exploration:** none
**Amends:** [ADR 036](036-docker-image-ci-ghcr.md) (the overlay is no longer the only pull path); [ADR 059 (LUM-184)](059-lum-184-first-run-quickstart-published-ghcr.md) (the quickstart now leads with the two release files, not the overlay)
**Related:** [ADR 037](037-ghcr-publish-public-repo-only.md), [ADR 049](049-slsa-artifact-attestations-ghcr.md), [ADR 247](247-lum-853-default-install-first-run.md), [ADR 261](261-ghcr-publish-workflows-travel-with-export.md); LUM-894, LUM-981

## Context

A household installed Lumogis by cloning the repository and running `docker compose up -d`, which built five images on their machine. `docker-compose.ghcr.yml` could pull two of them (orchestrator and web), but the stack still needed the checkout: Postgres read `postgres/init.sql` from it, Caddy read its `Caddyfile` and preflight, and stack-control, backup, Caddy and a curl-wrapped Qdrant were built locally. Updating meant `git pull` and a rebuild.

The model Thomas chose: install from two files downloaded from a GitHub Release, and update with `docker compose pull && docker compose up -d`.

## Decision

**A household installs from two release files and builds nothing.**

- **The release files.** Each GitHub Release carries `docker-compose.yml` and `example.env` (saved as `.env`), both attested, plus `SHA256SUMS` and `image-digests.txt`. `scripts/release/build_release_compose.py` generates the compose file from the repo's `docker-compose.yml`: every Lumogis service pulls `ghcr.io/lumogis/<image>:${LUMOGIS_VERSION:-release}` with no `build:`; profile-only services are dropped; the three mounts whose files are baked into images are dropped, and any other mount, `env_file`, `extends`, `include`, `configs` or `secrets` that would read from a checkout is an error. stack-control is given the install folder as `${PWD}` (environment and a same-path read-only mount), so a restart from Settings recreates containers with the host's paths.
- **Five published images.** `lumogis-orchestrator`, `lumogis-web`, `lumogis-stack-control`, `lumogis-backup` and `lumogis-caddy` are built and attested by `publish-image.yml` (amd64 and arm64). Qdrant, Postgres and Ollama are stock images; Qdrant's health check uses bash instead of a curl wrapper. Every image records `LUMOGIS_IMAGE_VERSION` (`X.Y.Z`, the short commit on `main`, `dev` for a local build), which backups store.
- **Self-contained images.** The orchestrator image carries `postgres/init.sql` and the migrations. On a database where `file_index` does not exist it applies `init.sql` in one transaction before the migrations; any other database never runs it. When no schema can be created (baseline missing or failing, no migrations directory) the runner exits 3 and the entrypoint stops with `FATAL: no database schema` — the one exception to the entrypoint's warn-and-continue on a failed migration ([ADR 059 (LUM-184)](059-lum-184-first-run-quickstart-published-ghcr.md)). The Caddy image carries its `Caddyfile` and preflight.
- **Tags.** `publish-image.yml` pushes `X.Y.Z` on a version tag and `latest`/`main` on public `main`. Only `release-assets.yml` moves `release` and `X.Y`.
- **Release order.** Push public `main` first (it builds `latest`: the build rehearsal), then the tag, then a **draft** Release, then dispatch `release-assets.yml` from `main`. It resolves each tag anonymously (a private package fails at once), requires amd64 and arm64, checks each digest's build provenance, checks the orchestrator's baked compose fingerprint against the generated file, pulls the generated file's images anonymously by digest, attests and uploads the four files in one call, moves `release` and `X.Y` (stable versions that are the newest release only, orchestrator last), and publishes the Release. Until then the Release is a draft and `releases/latest/download/` serves the previous pair. A prerelease's `example.env` pins its own version.
- **A stale compose file is flagged.** The release header carries a fingerprint of the file; the orchestrator image carries the same value. When they differ the orchestrator logs `compose_file_stale` and Admin → Diagnostics shows a warning. It never blocks start-up.
- **Checkouts still build.** The repo compose names each Lumogis image `ghcr.io/lumogis/<image>:${LUMOGIS_VERSION:-dev-${COMPOSE_PROJECT_NAME}}` with `pull_policy: build`, so contributors, CI and `verify-public-rc` always run their own code. The tag is per Compose project, so a test stack's build never replaces the image a running stack recreates from. `make update` pulls the images it does not build, rebuilds the rest, and stops before touching the stack if the build fails. `docker-compose.ghcr.yml` pulls all five instead (`build: !reset null`, `pull_policy: !reset null`).
- **Settings restarts keep local changes.** With no `COMPOSE_FILE` in `.env`, stack-control adds `docker-compose.override.yml` when it exists, as Compose does on the host.

## Alternatives considered

- **Keep the overlay as the pull path and document it better.** Rejected: it still needs a checkout, and two of five images.
- **A `dry_run` input on `publish-image.yml`.** Rejected (review D1.4): it conflicts with the guard's no-`if:` attest rule. The push of `main` is the rehearsal.
- **Publish the Release on the tag push and attach files afterwards.** Rejected: the upload raced the Release and the image builds, and `latest/download` could serve a mixed pair. The draft-then-publish order closes that.
- **A shared `:dev` local tag** (the plan's first choice). Changed during implementation: Compose's old per-project names had kept stacks on one host apart, and a shared tag let a test run's build leak into a running stack's Settings restarts.
- **`pull_policy: missing` in the overlay and rollback file** (the plan's first choice). Changed during implementation: with `missing`, `docker compose pull` skips an image already present, so an update never fetches a new `latest`, and a rollback never pulls a pinned image that has left the host.
- **The freshness notice as a store row in diagnostics** (the plan's first choice). Changed: the store status has no "stale" value and a stale file is not a store outage; a diagnostics warning needs no API change.
- **Refuse a tag that is not an ancestor of `main`** (review D5.2). Dropped: only Thomas dispatches, and tags come only from the release export. The job does require the dispatch ref to be `main`.

## Consequences

- An install from the release files has no `make`; the backup, restore and update commands are documented without it (`docs/deployment/upgrading.md`).
- In a checkout, `docker compose up -d` always runs a build (a cache hit when nothing changed).
- `LUMOGIS_VERSION` is the image tag. In a checkout, leaving it unset is the normal case; `IMAGE_TAG` is read by the overlay for one more release.
- Installs that use LibreChat, the knowledge graph overlays or the GPU, speech or LiteLLM overlays stay on a checkout until those overlays are published as release files.
- Sign-in stays off by default (ADR 247); turning it on by default is LUM-981.
- The first release that uses this flow runs it as a prerelease (`-rc.1`) end to end, then the release, then MS-001 on a clean host (LUM-980 Pass 4).

## Status history

- 2026-11-15: Finalised by /verify-plan — Passes 1–3 implemented and verified (unit, integration, export gates, RC integration, a local two-file install rehearsal); deviations above recorded; the release proof is Pass 4.
