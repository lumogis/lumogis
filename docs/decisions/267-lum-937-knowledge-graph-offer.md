# ADR-267: Explain and offer the knowledge graph in admin settings (LUM-937)

**Status:** Finalised
**Created:** 2026-11-16
**Last updated:** 2026-11-16
**Decided by:** /explore (claude-opus-5-5, headless run for LUM-937)
**Finalised by:** /verify-plan 2026-11-16 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-937-knowledge-graph-admin-page.plan.md`
**Exploration:** `.cursor/explorations/LUM-937-knowledge_graph_offer.md`
**Draft mirror:** `.cursor/adrs/LUM-937-knowledge_graph_offer.md`
**Linear:** LUM-937
**Extends:** [ADR 227](227-lum-881-privacy-question-where-blocked.md) (meets its revisit condition "LUM-855 ships and `GRAPH_MODE` becomes readable at runtime by the Web"), [ADR 229](229-lum-855-kg-agpl-relicence.md) / [ADR 239](239-lum-855-kg-publication-as-built.md) (graph is AGPL and optional), [ADR 247](247-lum-853-default-install-first-run.md) (first run on single-user installs is in-page)

## Context

The knowledge graph has been AGPL and in the public export since 0.12.0, so there is no longer a commercial reason to hide it, but a default install gives no sign it exists or what it costs. Lumogis Web cannot read `GRAPH_MODE`; the graph needs FalkorDB plus the `lumogis-graph` service (capped at 4 GB) on top of a base stack documented at 8 GB; only `GRAPH_MODE=service` works on the standard image; and the default install since 0.13.0 (two Release files, ADR 262) cannot run the graph at all, because `lumogis-graph` is not a published image and no graph overlay ships with the Release. The onboarding modal never renders on the default single-user install (ADR 247). Lumogis Server and the paid line are paused (2026-11-14).

## Decision

`GRAPH_MODE` stays `disabled` by default in every edition. Lumogis Web gains an admin-only **Knowledge graph** page in admin settings (`/admin/knowledge-graph`), backed by a new admin-only `GET /api/v1/admin/knowledge-graph`. The route reports the effective and configured graph mode, the graph service's health from the capability registry (`lumogis-graph`), and a memory verdict: total memory (`psutil.virtual_memory().total`, the host's or Docker VM's memory; Core's own cgroup `memory.max` is not read, because it is Core's limit, not the engine's) against a fixed 12 GiB. The page explains what the graph adds and costs. Where memory is enough, it offers the written `GRAPH_MODE=service` steps for a source checkout and says that installs from the Release files cannot turn it on yet. Where memory is short, it explains instead of offering. Once the graph is on and healthy, it says so, and it names a configured-but-degraded mode (for example `inprocess`, which does not load on the standard image) plainly. No onboarding-modal step is added; the issue's "first run" is narrowed to admin settings for the reasons in ADR 227 and ADR 247. Nothing is said to non-admin members.

## As built (LUM-937)

- **Route:** `orchestrator/routes/api_v1/admin_knowledge_graph.py` (router-level `require_admin`); read model `orchestrator/services/graph_status.py`; models `AdminKnowledgeGraphStatus` / `AdminKnowledgeGraphService` / `AdminKnowledgeGraphMemory` in `orchestrator/models/api_v1.py`.
- **State** (server-computed, first match wins): configured `inprocess` → `unsupported_mode`; configured `service` but effective not `service` → `service_wiring_failed`; effective `service` and the registry's `lumogis-graph` entry registered and healthy → `on`; effective `service` otherwise → `service_unreachable`; memory short → `off_low_memory`; else `off` (memory enough or unknown).
- **Memory floor:** `GRAPH_MIN_TOTAL_MEMORY_BYTES = 12 GiB` compared with `MEMTOTAL_TOLERANCE = 0.95`, because `MemTotal` reads below the nominal size. Derived, not measured (comment at the constant).
- **Response** carries mode tokens, booleans, a timestamp, a coarse registry reason code and two byte counts. No URL, secret or env value (sentinel and exact-key tests).
- **Web:** `clients/lumogis-web/src/features/admin/AdminKnowledgeGraphView.tsx`, pure copy in `knowledgeGraphCopy.ts` (the `privacyBlockCopy.ts` pattern), API module `src/api/knowledgeGraph.ts` pinned to the OpenAPI snapshot by a contract test. Registry reason codes are mapped to fixed text; unknown codes are not echoed.
- **Docs:** `docs/deployment/quickstart.md#turn-on-the-knowledge-graph` holds the same `.env` lines as the page and the `docker-compose.premium.yml` header.
- **Diagnostics `graph` row:** in service mode its message now says Core keeps no graph store and points to Admin → Knowledge graph. Its status stays `not_configured`, because the row feeds `stack_status` → `/api/v1/health` → the member graph-down banner (docstring note in `services/admin_diagnostics.py`).
- **Correction to the exploration:** `GRAPH_MODE=inprocess` on the standard image does not fall back to `disabled`. `plugins/graph/` is in the image, so `find_spec("plugins.graph")` succeeds and the plugin then fails to load. The page therefore keys `unsupported_mode` on the **configured** mode. `.env.example` and `docs/capabilities.md` were corrected to match.

## Alternatives Considered

Full detail in `.cursor/explorations/LUM-937-knowledge_graph_offer.md`.
- **On by default (checkout installs):** raises everyone's memory floor by up to 4 GB plus FalkorDB, and does nothing for Release installs.
- **Explanation only, no steps:** fails the theme's demo step 1.
- **`graph_mode` on the feature-flags route:** flags are `LUMOGIS_FF_*` booleans; health and memory don't fit.
- **Field on admin diagnostics:** heavy aggregate, and its `graph` store row is wrong in service mode.
- **Field on `/health` or a member route:** leaks deployment detail to non-admins.
- **Memory from `stack-control` `docker info`:** same figure through the Docker-socket sidecar, more risk.
- **`MemAvailable` instead of total:** the verdict would flicker with Ollama model loads.
- **Minimum with Core's cgroup `memory.max`:** inside the container that is the orchestrator's own limit, so it could only under-report the memory the graph containers share.
- **Admin-only onboarding-modal step:** reaches only sign-in-on installs, before there is data.
- **A button that turns the graph on:** deferred by Thomas (2026-11-12); LUM-937 Later.

## Consequences

- **Easier:** admins can see whether the graph is on, why it is not, and how to turn it on, without reading env files. Later graph-only surfaces (LUM-196 first) have one status read to use instead of their own `GRAPH_MODE` checks.
- **Harder / foreclosed:** the page is read-only; turning the graph on still means editing `.env` and restarting. Release-file installs are told they cannot turn it on until the graph image and overlay ship. The memory verdict uses total engine memory, not free headroom, so a busy host may be offered a graph it struggles to run; the copy says "about 4 GB more" so the admin can judge.
- **"On" depends on the capability registry.** A service-mode install that sets `KG_SERVICE_URL` but omits `CAPABILITY_SERVICE_URLS` has working webhooks yet reads `service_unreachable`; the copy names `CAPABILITY_SERVICE_URLS` as part of the setup. Right after a restart the registry may take up to five minutes to discover the service.
- **Future chunks must know:** the 12 GiB threshold is derived (8 GB base + 4 GB graph cap), not measured. The diagnostics `graph` row reports Core's local store and reads "not configured" on a working service-mode install; don't use it as "is the graph on". The `state` enum is the contract LUM-196 reads; add a state only with a new copy row and a contract-test update. No per-edition decision is made for Lumogis Server or the paid line.

## Revisit conditions

- When `lumogis-graph` is published to GHCR and a graph overlay ships with the Release: replace the "Release installs cannot turn it on yet" copy with Release steps.
- When LUM-937 Later's "turn it on with a button" is picked up: the page gains a write path through `stack-control`, and this ADR is superseded on that point.
- When Lumogis Server resumes: decide its graph default (its native install has no FalkorDB today).
- If a measured graph footprint on a real household library differs from 4 GB by more than about 1 GB: change the threshold.
- If ADR 247's revisit conditions fire (the onboarding modal renders on default installs): reconsider an admin graph pointer in the modal.
- If `GRAPH_MODE=inprocess` is retired or ships in the standard image (LUM-937 Later): update `derive_state` rule 1 and the degraded-mode copy together.

## Status history
- 2026-11-16: Draft created by /explore (headless, LUM-937)
- 2026-11-16: Revised during /review-plan --self (claude-opus-5-5) — memory verdict no longer takes the minimum with Core's cgroup `memory.max`: inside the container that file is the orchestrator's own `mem_limit`, so it could only under-report the memory the graph containers share (the exploration's research already noted this).
- 2026-11-16: Finalised by /verify-plan — implementation confirmed decision (`agent/lum-937`; `make test` 7151 passed, `make web-test` 1617 passed, OpenAPI snapshot in sync)
