# Release manual verification checklist

Operator-level checks that automated release gates (`make verify-public-rc`, `make verify-public-rc-full`) and coverage matrices cannot fully replace. Use this document together with [docs/testing/automated-test-strategy.md](testing/automated-test-strategy.md) and [docs/testing/README.md](testing/README.md).

## Purpose

Lumogis ships layered **automated** verification (unit tests, RC Compose stacks, export hygiene, Playwright where configured). Some behaviours still require **human confirmation** on a real host — fresh GHCR pulls, platform-specific compose boot, LLM routing privacy, and subjective product readiness.

**Rule:** If a product behaviour has neither automated test evidence in a coverage matrix row **nor** a row in this checklist, treat it as **untested** for release purposes.

Store API keys and credentials in environment / `.env` only — never commit secrets to git.

## When to run

**Public / AGPL operators**

- After automated gates on a **release candidate** tree when you maintain one (see [automated-test-strategy.md](testing/automated-test-strategy.md) — *Dev vs `main`*).
- For **GHCR pull-based** installs: run this checklist on a **fresh host** (no prior Lumogis volumes) using published images per [docs/capabilities.md](capabilities.md) and `docker-compose.ghcr.yml`.

**Maintainers (private checkout only)** — promote/publish workflow docs under `docs/release/` (not shipped in the public AGPL export) may require a completed sign-off from this file before fast-forwarding `main` or publishing to `lumogis/lumogis`.

## Sign-off block

Copy into your release notes or maintainer log when complete.

| Field | Value |
| --- | --- |
| Release version / git SHA | |
| Tester | |
| Date (UTC) | |
| Platforms tested (OS + arch) | |
| Automated gate | `make verify-public-rc-full` pass? (Y/N + log path) |
| Checklist result | All applicable MS rows pass / N/A documented |

## N/A rule

Any skipped checklist row must be marked **N/A** with a **one-line reason** in the sign-off table above or in that row's **Notes** column below.

## Checklist

| Done | ID | Check | Preconditions | Pass | Fail | Notes (N/A reason) |
| --- | --- | --- | --- | --- | --- | --- |
| [ ] | **MS-001** | Published **GHCR** image boots on a **fresh** host | Clean VM/host; `COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml`; pull `ghcr.io/lumogis/lumogis-orchestrator` and `ghcr.io/lumogis/lumogis-web` at release tag/digest ([capabilities.md](capabilities.md)); `config/test.env.example` or operator `.env` | `docker compose up -d` healthy; services running | Image pull/boot errors, crash loop | |
| [ ] | **MS-002** | `docker compose up -d` on **macOS or Linux** | Compose files per release docs; sufficient disk/RAM | All required services **running** | Exit non-zero, missing service | |
| [ ] | **MS-003** | `GET /health` returns **200** within **30s** of boot | Core URL known (Caddy/orchestrator) | 200 + healthy body | Timeout, 5xx, wrong routing | |
| [ ] | **MS-004** | **First-run document ingest** on **clean volume** | Sample doc in ingest path; graph mode per release config | Ingest success; no fatal orchestrator errors | Stuck job, 5xx, corrupt index | |
| [ ] | **MS-005** | **Local LLM** stays local (no cloud egress) | `config/models.yaml` + env: local/Ollama only; run one chat completion | Orchestrator logs show local provider; no outbound calls to configured cloud endpoints during the prompt | Cloud API traffic when local-only expected | |
| [ ] | **MS-006** | **Cloud LLM** path works when configured | Valid API key in env; cloud model enabled in `models.yaml` | Successful completion in UI or API | Auth errors, timeouts | N/A if release slice is local-only |
| [ ] | **MS-007** | **Meeting brief** produces valid Markdown | Feature enabled; fixture data if needed | Non-empty Markdown output | Empty/error output | N/A if brief feature not in this release |
| [ ] | **MS-008** | **Admin dashboard** non-empty after ingest | Web UI reachable; MS-004 complete | Dashboard shows data | Blank/broken UI | |
| [ ] | **MS-009** | **Backup/restore round-trip** preserves graph (**LUM-185** / **LUM-484**) | `make compose-test-backup` on maintainer machine (postgres+qdrant+falkordb volume wipe) | Restore after wipe; spot-check seeded entity + Qdrant point + FalkorDB `GRAPH.QUERY`; daily verify checks FalkorDB RDB envelope + checksum (not module load; functional graph proof is the round-trip). Bump `FALKORDB_IMAGE` together with backup sidecar build. | Data loss, broken graph | N/A if backup path not yet shipped |
| [ ] | **MS-010** | `docker compose down && up` **preserves** state | Prior successful ingest; named volumes | Data still present after cycle | Wiped volumes / empty graph | |

## Relationship to coverage matrices

Manual behaviours map to **MS-###** IDs cited in [TEST-COVERAGE-MATRIX-core.md](testing/TEST-COVERAGE-MATRIX-core.md) and sibling matrices ([testing README](testing/README.md)).

**Hub code-signing** rows (`4.2.4`, `4.2.5` in the private Hub matrix) remain **`MS-TBD pending LUM-408`** until a signing procedure is documented.

## Related docs

- [CONTRIBUTING.md](../CONTRIBUTING.md) — contributor testing and matrix maintenance
- [automated-test-strategy.md](testing/automated-test-strategy.md) — automated layers and RC gates
- [capabilities.md](capabilities.md) — GHCR pull deployment
