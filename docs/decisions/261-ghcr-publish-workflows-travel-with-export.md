# ADR 261 — GHCR publish workflows travel with the export (amends ADR 037)

**Status:** Finalised
**Created:** 2026-11-14
**Last updated:** 2026-11-14
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-14 (Claude Code)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/ghcr_publish_workflows_in_export_retro.md`
**Draft mirror:** `.cursor/adrs/ghcr_publish_workflows_in_export.md`
**Amends:** [ADR 037](037-ghcr-publish-public-repo-only.md) (decision 1)
**Related:** [ADR 036](036-docker-image-ci-ghcr.md), [ADR 049](049-slsa-artifact-attestations-ghcr.md); LUM-225, LUM-228, LUM-276

## Context

ADR 037 put `publish-image.yml` in `lumogis/lumogis` only and deleted it from `lumogis-app`. LUM-228 and LUM-276 later added `attestation-guard.yml` and `attestation-verify.yml` beside it, also only in the public repo.

The public repo is not edited by hand between releases. `/publish-private-main-to-public` builds an export tree from private `main` and syncs it with `rsync -a --delete --exclude '.git'`. A file that exists only in the public repo is therefore deleted by the next release. The 0.5.0 release commit (`4dcbc5f`, 2026-06-06) removed all three workflows.

No image was published after that. `ghcr.io/lumogis/lumogis-orchestrator:latest` and `lumogis-web:latest` stayed on a build of public `10785c4` from 2026-06-02, and no release got a version tag. The quickstart, `.env.example`, `docs/capabilities.md` and `SECURITY.md` kept describing images published on every release. Installs that build locally (the default) were not affected. Found while publishing 0.12.0.

## Decision

The three workflows live in `lumogis-app` and reach the public repo with the export, like every other public file. The trust boundary of ADR 037 is unchanged: images are built only from the public `lumogis/lumogis` tree, by one publisher.

- **Every job is guarded on `github.repository == 'lumogis/lumogis'`.** In `lumogis-app` the workflows trigger and every job is skipped (verified on private `main` `bc373ed9`: Publish image, Attestation guard and Attestation verify all `skipped`). `publish-image.yml` already had the guard; the two attestation workflows gained it.
- **Triggers are as before:** push to public `main` (tag `latest`), push of a `v*` tag (semver tag), and a manual run on `main`.
- **A manual run on `main` takes an optional `release_version`** (for example `0.12.0`, checked as `N.N.N`), which adds that version tag. This covers a release whose tag points at a commit without the workflow, as `v0.12.0` does.
- **All third-party actions are SHA-pinned** (ADR 037 decision 2): `upload-artifact` and `download-artifact` were on `@v4`.
- **`attestation-verify.yml` reads `publish_run_id` from `env:`** and requires a number, instead of interpolating it into the script.
- **Tests pin it** (`orchestrator/tests/test_check_public_export_script.py`): the three files exist, are not on the strip list, appear in the export tree, and every job carries the guard. Removing a file or a guard fails `make test` before promotion.

ADR 037 decisions 2–5 stand. Its decision 1 now reads: the workflow **runs** only in `lumogis/lumogis`; it is **kept** in `lumogis-app`.

## Alternatives considered

- **Exclude the three files from the rsync** (`--exclude` in the publish skill). Not chosen: it keeps them public-only, so any sync that forgets the exclusion deletes them again, and nothing in the private tree shows they exist.
- **Strip them from the private tree and re-add them by hand after each release.** Not chosen: that is the manual step that was missed for five months.
- **A required-path check in `check-public-export.sh`** (like LUM-303 and LUM-433). Not chosen for now: every minimal fixture tree in the export tests would need the files. The pytest guard covers the same failure earlier.

## Consequences

- The public workflows are edited in `lumogis-app` on `dev`, reviewed and promoted like other code. Editing them directly in the public repo is overwritten by the next release.
- Each release push to public `main` publishes `latest`, and pushing the release tag publishes its version. A release tag created on a commit without the workflow needs a manual run with `release_version`.
- `lumogis-app` shows skipped runs for the three workflows on pushes to `main`. They use no runner time.
- The 0.12.0 images were built by a manual run on public `main` at `01d40db`. It holds the 0.12.0 code plus docs and workflow commits only.

## Revisit conditions

- The export stops being an rsync with `--delete` (for example per-file commits), or the public repo starts accepting changes that do not come from the private tree.
- A second publisher is proposed, for example the paused Lumogis Graph or appliance images (LUM-261).
- GitHub changes how `github.repository` behaves for forks or mirrors, so the guard no longer separates the two repositories.

## Linear linkage (Product OS)

- **Existing issue:** [LUM-225](https://linear.app/lumogis/issue/LUM-225/migrate-publish-imageyml-to-public-repo-images-must-only-build-from) (Done 2026-05-13). This restores its outcome; its acceptance line "publish-image.yml is removed from lumogis/lumogis-app" is superseded by this ADR.
- **New issue needed:** no. Fixed now (dev `05c6e91e`, private main `bc373ed9`, public `01d40db`); guard tests in this change.
- **Historical evidence only:** no.

## Testing retrospective

- **Run:** actionlint 1.7.12 on the three files (exit 0); the attestation guard's structural step, run locally with yq 4.45.4 (exit 0); `make test-export-gates` 34/34; `check-public-export.sh` OK on the export tree and on the public checkout; `test_deployment_hardening_config.py` + `test_check_public_export_script.py` 79 passed; the new guard tests 3 passed, and fail when a job's guard is removed.
- **Live:** private `main` runs all skipped; publish run `43650756372` on public `01d40db` with `release_version=0.12.0`.
- **Gap:** nothing checks that a release actually produced an image. `attestation-verify.yml` only runs after a publish that happened.

## Status history

- 2026-11-14: Finalised by /record-retro (retrospective).
