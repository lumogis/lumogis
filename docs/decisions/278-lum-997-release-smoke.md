# ADR-278: Release smoke — automated release checks on amd64 and arm64 gate publishing (LUM-997)
**Status:** Finalised (implementation refinements recorded in "As built"; the lumogis-cloud CI scope is not built yet)
**Created:** 2026-11-18
**Last updated:** 2026-11-18
**Decided by:** /explore (claude-opus-5-5, headless run-workflow); owner decision Thomas 2026-11-18 (release checks run in CI, not by hand)
**Finalised by:** /verify-plan 2026-11-18 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-997-release-smoke.plan.md`
**Draft mirror:** `.cursor/adrs/LUM-997-release_smoke.md`
**Exploration:** `.cursor/explorations/LUM-997-release_smoke.md`
**Linear:** LUM-997
**Amends:** [ADR 262](262-lum-980-install-from-published-images.md) (install from the release files): `release-assets.yml` publishes only after the smoke. Works within [ADR 261](261-ghcr-publish-workflows-travel-with-export.md) (public-only workflows kept in lumogis-app, guarded on `github.repository`) and [ADR 037](037-ghcr-publish-public-repo-only.md) (SHA-pinned actions).

## Context

Every public release ended with a person running `docs/RELEASE-MANUAL-CHECKLIST.md` MS-001 to MS-011 on a fresh host, including arm64 on an Apple Silicon Mac. On 2026-11-18 Thomas decided release checks must run in CI ("I will not run those manual checks with every public release"). Since ADR 262 a household installs from two Release files. Since ADR 271 / 275 / 277 that install also carries the graph switch, the secure address and voice. `release-assets.yml` is the only thing that publishes a Release. Workflows that act on releases run only in `lumogis/lumogis` (ADR 261). GitHub-hosted `ubuntu-24.04` and `ubuntu-24.04-arm` runners are free on public repositories. The secure-address check touches the operated name service, whose first-certificate budget is small (35 a week, 3 per IP).

## Decision

`release-assets.yml` becomes `stage → smoke → publish`. The `smoke` job calls a new reusable `release-smoke.yml`, which also has a dispatch trigger for rehearsals. On `ubuntu-24.04` and `ubuntu-24.04-arm` in parallel, it installs from the staged two files and runs a stdlib-Python smoke script (`scripts/release/smoke/release_smoke.py`) through the household API. The suites are `install` (MS-001–005, 008, 010, 011 and voice), `graph`, `upgrade` (update and going back, including MS-009), `secure-address` and an optional `cloud-llm`. The Release is published only when the smoke is green.

MS-005 is proved by rejecting and counting new connections from the orchestrator's container address to non-private destinations during a local chat completion, with a negative control. The secure address uses a CI-scoped registration value in the name service (lumogis-cloud). That value gives CI households their own daily budget and removes them after one hour; Core uses it via `LUMOGIS_HOME_DNS_REGISTRATION_SECRET` with `LUMOGIS_HOME_DNS_ACME_STAGING=true`. The Ollama volume is not cached. The manual checklist keeps only rows that cannot run in CI, each saying why.

## Alternatives Considered

Full detail is in `.cursor/explorations/LUM-997-release_smoke.md`.

- **Standalone smoke that the publish skill reads:** the gate would be a person following a step, and a stable release would be published before it is checked.
- **Smoke steps inline in the single release-assets job:** one runner, so no arm64.
- **Private self-hosted runners:** amd64 only, not a clean host, and they break ADR 261.
- **Compose `internal` network for MS-005:** changes the topology under test, and the stack needs the internet for pulls and ACME.
- **Passive egress log for MS-005:** proves less, so it is kept only as a diagnostic.
- **Egress-guard flag on for MS-005:** circular.
- **Third-party runner egress agent:** a new agent inside the publishing workflow.
- **Fixed CI device key instead of a CI scope:** permanent households with no cap or expiry. It is the fallback if the lumogis-cloud change slips.
- **Skipping the secure address in CI:** it is the most install-sensitive path.
- **Caching the Ollama volume:** the first-run pull would no longer be tested, and a cache is a poisoning vector in a release-gating workflow.
- **A GitHub environment approval for the emergency skip:** with one maintainer it would be self-approval; the skip instead must name an issue and is recorded with the actor.

## Consequences

**Easier:**
- Every release, stable or rc, is proven on a clean amd64 and arm64 install of the exact published images before households can download it.
- Thomas's Mac and an hour per release are no longer needed.
- LUM-1005 P1/P2 and the release-install side of LUM-990 / LUM-991 are re-proven automatically.

**Harder:**
- A release takes about 45–60 min longer.
- A flaky smoke blocks publishing, except through an explicit, recorded skip reason (`skip_smoke_reason`, one line naming `LUM-###`, written into the Release notes with `github.actor`).
- The workflow cannot be proven before an export: before it, only actionlint, unit tests and a local rehearsal against a published version are possible. The publish skill makes a rehearsal dispatch against the current release **mandatory and green** before the first rc that carries the gate; the first real gated run is that rc.
- The name service gains a CI scope that must stay private; staging-only cannot be enforced by the Worker, only deterred by the cap and the one-hour life.
- Runner disk is tight (about 13–15 GB per install against 14 GB listed for arm64), so image or model growth can break the smoke before it breaks a household.

**Future chunks must know:**
- New household-visible behaviour needs either a smoke check or a checklist row explaining why it cannot be automated.
- The smoke drives the public HTTP API, so breaking changes to setup/claim, auth login, ingest upload/jobs, `/search`, chat, voice, diagnostics, secure-address, knowledge-graph admin, `/settings` and `/settings/restart` break the release gate and must update `release_smoke.py` in the same change.
- Compose service names (`orchestrator`, `stack-control`, `backup`, `ollama`, `caddy`, `falkordb`, `lumogis-stt`), the volume names and the backup/restore script interface are contracts of the smoke.
- Check ids (`MS-001`, `VOICE`, `GRAPH-RESTORE`, …) are cited by the checklist and the coverage matrix; rename only with both (`test_every_catalogue_id_is_in_the_checklist`).
- `make verify-public-rc[-full]` stays as the pre-export gate on private `main`; the smoke is the post-export, pre-publish gate.

## Revisit conditions

- If the arm64 runner cannot hold one full install after cleanup, move arm64 to a larger or third-party runner (R3), or split the suites across arches.
- If GitHub-hosted macOS runners gain Docker, add MS-002 Docker Desktop to the smoke and remove its checklist row.
- If the smoke regularly takes more than 75 min, revisit the Ollama cache (C2).
- If a CI registration value leaks, or a production certificate is ever issued for a CI name (seen in CT logs), pin CI names to a staging ACME account with CAA `accounturi`, or rotate the value.
- If Linear, the publish skill or GitHub adds a native Release-protection gate, consider moving the gate there.

## As built (2026-11-18, /verify-plan)

- **Upload moved into `publish`.** `stage` resolves, attests and hands the four files to the smoke as an artifact; it no longer writes to the Release (`packages: read`, no `gh release upload|edit`). `publish` downloads the artifact, re-checks `SHA256SUMS`, uploads, appends a `Release checks:` line to the notes (replaced, not duplicated, on a re-dispatch), moves `release` / `X.Y` and then checks both tags anonymously against `image-digests.txt`. A red smoke therefore leaves a draft without new files, and a `replace_published` re-dispatch leaves the published files as they were.
- **The skip is decided inside the smoke.** `publish` has `needs: [stage, smoke]` with default success semantics and an `if:` that is exactly the repo + `main` guard (no `always()`, no `needs.*.result`). With `skip_reason` set, the smoke runs no suite and its `summary` job succeeds with `verdict=skipped`. Static tests assert this structure.
- **`runner` is a static matrix, `suite` is dynamic** (from the `files` job), because the export gate resolves `runs-on` statically and `matrix` is not available in a job-level `if:`. The export gate's runner allowlist now accepts `ubuntu-24.04-arm` / `ubuntu-22.04-arm` and still rejects lookalikes.
- **Smoke jobs check out `github.sha`**, not the release tag, so a rehearsal against a release older than the script works; images and install files come only from the staged artifact or the published Release.
- **Every suite pins `LUMOGIS_VERSION=<version>`** before the first `up`: a stable `example.env` says `release`, which moves only in `publish`.
- **Version skew:** `--expect stt,signin,graph-switch` (the gate's default) makes a missing feature a failure; a rehearsal against 0.13.x passes `--expect ""` and those checks report `na`.
- **`GRAPH-RESTORE` uses `compose down`** (not `stop`) before `docker volume rm`: Docker refuses to remove a volume still referenced by a stopped container, which would have turned the wipe into a silent no-op. It also asserts FalkorDB is empty after the wipe.
- **`MS-006`** sets the cloud key through the per-user connector-credential route (`PUT /api/v1/me/connector-credentials/llm_anthropic`), because `PUT /settings` `api_keys` answers 422 with sign-in on.
- **Not built yet:** the lumogis-cloud CI registration scope (plan Pass 3). Until Thomas deploys it and sets `SMOKE_SECURE_ADDRESS_REQUIRED=true`, the `secure-address` suite reports `na` and does not gate; without that variable it never registers, even when the secret exists.

Proven: 113 unit and static tests of the script and both workflows (fake Core, fake `docker` / `iptables` / `openssl` / `dig`), pinned actionlint 1.7.12, the attestation-guard `release-assets` yq lines (one attest step, no `if:`), `make test-export-gates` (37/37), `check-public-export.sh` on a fresh export tree, and a transcript of the voice fixture by the pinned Speaches 0.8.3 image (9/9 words). **Not yet proven:** any run on hosted runners (it can only run in `lumogis/lumogis` after an export), the local amd64 rehearsals against 0.13.0 / 0.13.1 (the dev host's Qdrant held port 6334), and the `graph` and `secure-address` suites live (0.13.x has neither feature, so their first live run is the first gated rc).

## Status history
- 2026-11-18: Draft created by /explore (headless, LUM-997)
- 2026-11-18: Refined during /review-plan --arbitrate R1 — upload moved into `publish`; skip decided inside the smoke; sign-in-off releases and the update from them handled (`UPGRADE-SIGNIN`); `GRAPH-RESTORE` added for the MS-009 graph round-trip; mandatory pre-tag rehearsal
- 2026-11-18: Finalised by /verify-plan — implementation confirmed the decision; refinements recorded in As built; lumogis-cloud CI scope still to build
