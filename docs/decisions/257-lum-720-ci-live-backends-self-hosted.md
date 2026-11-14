# ADR-257: CI live backends on the shared self-hosted runners (LUM-720)

**Status:** Finalised
**Created:** 2026-11-11
**Last updated:** 2026-11-11
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-11 (claude-opus-5-5)
**Plan:** none — shipped as a CI repair after LUM-874's verified plan (`.cursor/plans/archived/LUM-874-ci-runner-ephemeral-service-ports.plan.md`)
**Exploration:** `.cursor/explorations/ci_live_backends_self_hosted_retro.md`
**Draft mirror:** `.cursor/adrs/ci_live_backends_self_hosted.md`
**Relates:** [ADR 235](235-lum-869-web-checks-ci-lane.md) (web-checks lane on the same runners)

## Context

`.github/workflows/ci.yml` runs every job on two self-hosted runners, `lumogis-dev-1` and `lumogis-dev-2` (label `lumogis-laptop`, LUM-788). Both run as one OS user on one Docker daemon, on a VM that also runs a live Lumogis stack. `lint-and-test` runs the orchestrator suite with `pytest -x` and `LUMOGIS_REQUIRE_INTEGRATION_DB=1` against service containers (LUM-718).

From 2026-11-08 09:29 to 2026-11-11 10:53 every `dev` CI run was red, and the causes hid one another behind `-x`:

- a FalkorDB live test (LUM-54) that fails without FalkorDB, in a job that had none;
- `test_entrypoint_refresh_secret.py`, whose stripped subprocess environment dropped the `LD_LIBRARY_PATH` the runners' toolcache `python3` needs (7 failures, 1 visible);
- a stale OpenAPI snapshot (LUM-922);
- intermittent "failed to receive status … EOF" in `doctor-integration` and `backup-integration`, because `web-docker-build`'s buildx builder became every job's current builder and was removed mid-build;
- earlier, fixed host ports 55432 / 56333 held by a leftover container (LUM-874).

Separately, LUM-720 recorded that the cross-user isolation tests had no executing gate. Eleven live modules skipped on a connection failure even under the CI flag, and the paperless and IMAP live tests never ran on `dev`.

## Decision

The two runners are treated as **one shared environment**. Anything that is per-user or per-host is either job-scoped or serialised. Every backend a lane promises is present, and its absence is a failure.

- **Service containers publish the container port only.** Steps read the host port from `job.services.<id>.ports['<port>']` in step-level `env`, because the `job` context is not available in job-level `env`. `lint-and-test` runs `postgres:16`, `qdrant/qdrant:v1.14.1`, `falkordb/falkordb:v4.18.1` and `greenmail/standalone:2.1.0` this way. The Postgres provision step refuses an empty or default `PGPORT` before psql runs, and the Postgres and Qdrant steps print the port they got.
- **Unreachable means failure under the lane's flag.** Live integration tests call `require_postgres(exc, what=...)` (`orchestrator/tests/integration/_pgguard.py`), never `pytest.skip`, on a backend connection failure. A fixture host configured on purpose (`LUM811_IMAP_HOST`) counts as promised.
- **Opt-in live gates run where their backend runs.** `RUN_FALKORDB_BANK_ISOLATION`, `RUN_FALKORDB_ENTITY_EDGES` and `RUN_M1_COMPAT` are on in `lint-and-test`. `LUM811_REQUIRE` stays off, because it also unlocks a timing benchmark and an image property that belong to `make verify-lum-811`.
- **Builders are job-scoped.** `docker/setup-buildx-action` sets `use: false`, and the build step names `builder: ${{ steps.<id>.outputs.name }}`.
- **Fixture lanes with fixed resources run one at a time.** `paperless-integration.yml` runs on the self-hosted runners, on `dev` as well as `main` pushes (path-gated). It has one concurrency group across refs with `cancel-in-progress: false`.
- **Subprocess tests that run `python3` carry `LD_LIBRARY_PATH`.**

### As-implemented surface

- `.github/workflows/ci.yml` — `lint-and-test` services and step env (including `CI_QDRANT_PORT` and `FALKORDB_URL`; `LUM811_IMAP_HOST`, `LUM811_IMAP_PORT`, `LUM811_SMTP_HOST` and `LUM811_SMTP_PORT`; and the three `RUN_*` flags); `web-docker-build` buildx step.
- `.github/workflows/paperless-integration.yml` — `runs-on`, push branches, concurrency, install-step `TMPDIR`.
- Guards in `orchestrator/tests/test_deployment_hardening_config.py`: `test_ci_lint_and_test_services_use_ephemeral_host_ports` and `test_ci_buildx_builders_stay_private_to_their_job`.
- Coverage matrix row 1.9.26.

## Alternatives considered

- **Keep fixed host ports, serialise `lint-and-test` across refs.** Not chosen: it costs throughput and still fails on a container a cancelled run leaves behind.
- **Pre-job cleanup of stale containers in the runner hook (homelab).** Not chosen: with Docker-assigned ports a leftover container no longer breaks CI. It only uses memory.
- **A separate compose-based integration job** (LUM-720 option 2). Not chosen for now: service containers cover every live module except paperless, which already had its own lane.
- **Make the paperless fixture parallel-safe** (random ports, per-run project). Not chosen for now: the lane is path-gated and rare, so queueing is cheaper.
- **Keep the FalkorDB gates opt-in.** Rejected: one of them had gone stale without anyone seeing it.

## Consequences

- `dev` CI is a reliable signal: four consecutive green runs. `lint-and-test` passes 6918 tests with 0 failures, and integration skips are down from 24 to 15. The 13 paperless tests run in their own lane, and the 2 `verify-lum-811` checks are deliberately manual.
- A new live test that needs a new backend must add the service the same way: bare port, step-env expression, and an entry in the guard test. A new `setup-buildx-action` must set `use: false`. Both are enforced by tests.
- `lint-and-test`'s pytest step takes about 8 minutes, because it no longer stops at the first failure.
- The paperless lane queues behind itself across refs.
- The public export still rewrites self-hosted `runs-on` to `ubuntu-latest` (LUM-855 gates). Runner registration scope stays the control for the fork vector (LUM-856).

## Revisit conditions

- CI moves to a test-runner image (LUM-894 layer 1), which changes the toolcache `LD_LIBRARY_PATH` rule and possibly the service wiring.
- A third runner or a second OS user is added.
- `doctor-integration` or `backup-integration` collide across refs; they keep fixed off-default ports and the `lumogis-test` project name.
- The paperless fixture becomes parallel-safe, at which point the global concurrency group can go.

## Linear linkage (Product OS)

- **Owning issue:** LUM-720 (Done 2026-11-11).
- **Related:** LUM-874 (Done; ephemeral ports), LUM-894 (In Progress; layer 4 partly done), LUM-856 (open; runner registration scope, human check).
- **New issue needed:** no.

## Testing retrospective

- **CI-equivalent run on lumogis-dev:** the runner's toolcache Python, throwaway Postgres, Qdrant, FalkorDB and Greenmail, `LUMOGIS_REQUIRE_INTEGRATION_DB=1`, and no `-x`. Result: 6918 passed, 0 failed.
- **`make compose-test-paperless`:** 14 passed.
- **`make test-export-gates`:** 34 of 34.
- **Hermeticity lane:** 204 skipped, exit 0.
- **Negative proofs:** with Qdrant at a dead port, 8 errors (these used to be skips). With Greenmail at a dead port, 7 errors. Mutations on both guard tests were all caught.
- **Known gaps:** the Ollama live-embedder case, and fixed ports across refs in `doctor-integration` and `backup-integration`.

## Status history

- 2026-11-11: Finalised by /record-retro (retrospective).
