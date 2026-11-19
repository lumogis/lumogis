# ADR-268: Keep the previous images for `make rollback` with local rollback tags (LUM-984)

**Status:** Finalised
**Created:** 2026-11-16
**Last updated:** 2026-11-16
**Decided by:** /explore (claude-opus-5-5, headless run for LUM-984); revised at /review-plan arbitration R1 and R2
**Finalised by:** /verify-plan 2026-11-16 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-984-rollback-image-tags.plan.md`
**Exploration:** `.cursor/explorations/LUM-984-rollback_image_tags.md`
**Draft mirror:** `.cursor/adrs/LUM-984-rollback_image_tags.md`
**Linear:** LUM-984
**Amends:** [ADR 125](125-lum-187-rollback-pin-migration-gate.md) (decision 2: the recorded ref is now a local rollback tag, not a digest; decision 3: `rollback.sh` generates the override from the state file instead of `update.sh` storing it)
**Related:** [ADR 123](123-lum-187-update-mechanism.md), [ADR 248](248-lum-892-backup-uploads-capture-media.md) (LUM-892 added the rebuild under the same tag), [ADR 262](262-lum-980-install-from-published-images.md) (LUM-980 reset `pull_policy` in the override; release installs have no rollback scripts)

## Context

`make update` on a git checkout records each running service's image ref (`capture_rollback_state`, `scripts/update/common.sh`) and then, since LUM-892, rebuilds every Lumogis image under the same compose tag (`docker compose build --pull`). The recorded ref was either that moved tag or a local-only `repo@sha256:` digest. On the containerd image store (Docker 29's default, and the lumogis-dev host's store) the digest stops resolving once the tag moves, and the rollback override's `pull_policy: !reset null` (LUM-980) then sends Compose to the registry, which refuses. On the classic store the moved tag silently restarts the new version. Either way `make rollback` could not undo a bad update. The fix must work offline, stay inside the operator scripts, and not touch the release-install path (ADR 262), which has no rollback scripts.

## Decision

Before pulling or building, `capture_rollback_state` tags each running container's image ID as `lumogis-rollback/<compose project>/<service>:<UTC timestamp>` (one timestamp per capture; the project comes from the container's `com.docker.compose.project` label, because one Docker daemon serves several compose projects) and records that tag. Names that are not already `[a-z0-9]` runs joined by `-` are normalised and get a POSIX `cksum` suffix, so two different names never share a tag. The update stops if a tag cannot be made, and the capture is atomic: the previous state survives a failed capture, and the tags this capture already made are removed.

The state file (`previous-images.txt`, unchanged `service<TAB>image<TAB>ref` shape) is the only record: `rollback.sh` generates `rollback-compose.override.yml` from it each time it runs, so the two cannot disagree and a lost override is rebuilt. The override pins a rollback tag with `build: !reset null` and **`pull_policy: never`**. `rollback.sh` never runs `docker pull` for these refs, and checks that every pinned tag exists before the backup check and before stopping anything.

After a healthy update, `update.sh` removes this project's rollback tags from older captures (only refs that match the rollback-ref grammar count; plain `docker rmi`, never `-f`). An update that has not passed its health gate leaves a marker (`update-in-progress` beside the state file). When the next `make update` finds it, it runs the health gate once: if the stack still fails, it keeps the existing rollback state instead of recording the images of the unfinished update, so its prune cannot delete the last good images; if the stack passes now, it records the running images as usual, so a slow first boot does not leave rollback two versions back. `rollback.sh` clears the marker once its own health gate passes. An update that finds no running service removes the rollback state, as before. Refs in state files written before this change keep the old handling (`pull_policy: !reset null`, `docker pull`).

## Alternatives Considered

See `.cursor/explorations/LUM-984-rollback_image_tags.md`.

- **Rollback tag in the image's own GHCR repo (`…:rollback-<ts>`):** equally safe only with `pull_policy: never`, and it mixes rollback tags into published repo names and official `library/` names.
- **Pin the old image ID:** does not resolve on containerd once the tag moves (that is the bug itself).
- **A unique compose tag per update:** reopens the tagging contract that LUM-980 / ADR 262 just set, and every other compose call would need the same variable.
- **`docker save` tarballs:** GBs and minutes per update for what a tag keeps for free.
- **`docker commit`:** captures the writable layer, not the shipped image.
- **Write the capture to a pending file and promote it after the health gate** (for the re-run case): rejected at plan arbitration R1. Rollback is wanted exactly when the gate failed, so the state must already be current then. The unfinished-update marker keeps it current and stops a re-run from replacing it.
- **An unconditional keep while the marker exists:** rejected at arbitration R2. An update that fails its gate on a slow boot, followed weeks later by a routine `make update`, would have left rollback two versions back. The one-try health probe separates the two cases.

## Consequences

- `make rollback` restores the exact previous images on both image stores, offline. That includes the ghcr overlay, which no longer depends on GHCR still serving an old digest.
- An unqualified local name is never pulled. `docker.io/lumogis-rollback` is a namespace anyone can register, and the old override would have asked Docker Hub for it.
- `lumogis-rollback/` is a reserved local prefix. It lives in `common.sh` and `write_rollback_override.py`; `test_rollback_prefix_matches_common_sh` keeps the pair equal.
- One previous image per service stays on disk until the next healthy update.
- `docker image prune -a` / `docker system prune -a` can remove the rollback tags, and rollback then stops with a clear local error instead of trying a registry.
- Rollback pins every captured service, datastores included. That was already the behaviour (the old capture recorded the datastore digests too); `rollback.sh` and the upgrade guide now say that a rollback across a datastore version change needs `make restore`.
- A `docker compose build` without `up` leaves running containers on images that no longer exist; `make update` then stops before changing anything and says to run `make build` first.
- `rescue-stop-entities.sh` step 6b's first lookup (the container's own image ID) now succeeds during `make update`. Its fallback stays for `make build` / `make dev`.
- Docker-gated pytests (build v1 → capture → rebuild v2 under the same tag → the override runs and recreates v1; a missing tag under `pull_policy: never` asks no registry) run in CI `lint-and-test` on the self-hosted runners; an unreachable daemon fails there (ADR 257) and skips locally.
- Not proven by an automated test: a live `make update` → `make rollback` on a full stack (tracked as a `gate:pre-main` child of LUM-984), the classic image store, and macOS hosts (bash 3.2 portability is checked statically).

## Revisit conditions

- Docker adds a way to protect images from `prune -a` (a label-based keep filter honoured by default): consider using it for rollback tags.
- The checkout update stops rebuilding under a fixed tag (for example, a per-build tag is introduced for other reasons): the rollback tag becomes redundant.
- Checkout installs move to pulling published images only (no local build): GHCR digests would again be enough, and the local tag only saves the network.
- Docker changes short-name resolution so that unqualified names are no longer looked up on Docker Hub: `pull_policy: never` could be relaxed (it would still be the safer default).

## Status history

- 2026-11-16: Draft created by /explore (headless).
- 2026-11-16: Revised during /review-plan --arbitrate R1 — tag name namespaced by compose project and made collision-free; prune scoped to the project and to grammar-matching refs; atomic capture; an unfinished update keeps its rollback state; an update with no running services removes the state.
- 2026-11-16: Revised during /review-plan --arbitrate R2 — the override is generated by `rollback.sh` from the state file; the unfinished-update keep applies only while the stack fails a one-try health gate; datastore rollback stated as a consequence.
- 2026-11-16: Finalised by /verify-plan — implementation confirmed decision (one addition within intent: capture also cleans up when a container vanishes before `docker inspect`).
