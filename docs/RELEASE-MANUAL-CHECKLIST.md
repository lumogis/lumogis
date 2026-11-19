# Release verification checklist

Every per-release check runs by itself in the **release smoke** (`.github/workflows/release-smoke.yml`, script `scripts/release/smoke/release_smoke.py`). The smoke installs the release from its two Release files on clean amd64 and arm64 machines and checks it through the household API and the compose CLI. `release-assets.yml` publishes a Release only after the smoke is green. This document lists what the smoke covers and the few checks that cannot run in CI. Use it together with [docs/testing/automated-test-strategy.md](testing/automated-test-strategy.md) and [docs/testing/README.md](testing/README.md).

## Purpose

Lumogis ships layered **automated** verification (unit tests, RC Compose stacks, export hygiene, Playwright where configured), and since LUM-997 the **release smoke** proves the published install works on both architectures. The rows left for a person are not per-release checks, or cannot run on a GitHub-hosted runner, and each one says why.

**Rule:** If a product behaviour has neither automated test evidence in a coverage matrix row **nor** a row in this checklist, treat it as **untested** for release purposes.

Store API keys and credentials in environment / `.env` only — never commit secrets to git.

## Release record

Written by the publishing maintainer into the release log. There is no tester row: the smoke is the tester.

| Field | Value |
| --- | --- |
| Release version / git SHA | |
| `release-assets` run URL | |
| Release smoke summary | passed / skipped (reason, issue, who dispatched) |
| Date (UTC) | |

A release is published only when the smoke is green, or with a `skip_smoke_reason` that names an issue (`LUM-###`); the reason and the person who dispatched are written into the Release notes.

## Automated in the release smoke

The MS ids below stay valid: the coverage matrices cite them. Each maps to the smoke check that runs it. A check is **required** unless marked optional; `na` means the release does not have the feature (`--expect` makes a missing expected feature a failure, never a surprise).

| ID | What | Smoke check(s) | Suite |
| --- | --- | --- | --- |
| **MS-001** | The two release files install on a fresh host (LUM-980): within 15 min every service healthy; `up` printed no `Building`; the second `up` recreates only orchestrator, stack-control and backup; every Lumogis container runs the digest in `image-digests.txt` with `LUMOGIS_IMAGE_VERSION`; `pull && up -d` changes nothing | `FILES-001`, `MS-001`, `MS-001-DIGESTS`, `MS-001-STEADY` | install |
| **MS-002** | `docker compose up -d` on Linux (amd64 and arm64). The macOS half is manual, below | `MS-001` | install |
| **MS-003** | `GET /health` 200 and Lumogis Web through Caddy answer once healthy | `MS-003` | install |
| — | First visitor creates the admin (LUM-981) | `SETUP-CLAIM` | install, graph |
| **MS-004** | First-run document ingest on a clean volume, then search finds it | `MS-004` | install |
| **MS-005** | Local LLM stays local: one chat completion with outbound connections rejected and counted (negative control included) | `MS-005` | install |
| **MS-006** | Cloud LLM path works (optional: needs a spend-capped key) | `MS-006` | cloud-llm |
| **MS-007** | Meeting brief — N/A while the feature is not in the release | — | — |
| **MS-008** | Admin dashboard data: the **API half** — `/health` counts and the diagnostics routes (the browser half is not checked per release, below) | `MS-008` | install |
| **MS-009** | Backup/restore preserves data and the graph: wipe Postgres, Qdrant and FalkorDB volumes, restore, counts come back; and going back to the previous version with the documented commands | `GRAPH-BACKUP`, `GRAPH-RESTORE`, `UPGRADE-BACK` | graph, upgrade |
| **MS-010** | `down && up -d` keeps the data and the sign-in | `MS-010` | install |
| **MS-011** | stack-control's Docker CLI and Compose plugin match the host; a restart from Settings recreates the stack | `MS-011` | install |
| — | Dictation (LUM-995): the `stt` profile is on and Speaches runs; the speech model is ready; a sample sentence is transcribed (at least 7 of 9 words) | `VOICE-PROFILE`, `VOICE` | install |
| — | Knowledge graph on and off (LUM-989): turning it on needs no confirmation on a 16 GB runner, entities appear, turning it off leaves everything healthy | `GRAPH-ON`, `GRAPH-ENTITIES`, `GRAPH-OFF` | graph |
| — | Update from the previous stable release, including from sign-in off (0.13.x), turning sign-in on afterwards, and going back | `UPGRADE-PREV`, `UPGRADE-PULL`, `UPGRADE-SIGNIN`, `UPGRADE-BACK` | upgrade |
| — | Secure address with a staging certificate (LUM-331), once the name service has its CI scope | `SECURE-ADDRESS` | secure-address |
| — | Disk, memory and time of each runner, as evidence | `RUNNER-FOOTPRINT` | install, graph |

## Still manual

Each row says why it is not in CI.

| ID | Check | Why not in CI |
| --- | --- | --- |
| **MS-002-MAC** | `docker compose up -d` on **macOS** (Docker Desktop) | GitHub-hosted macOS runners have no Docker. Covered by everyday use, not per release. |
| **MS-008-UI** | The admin dashboard shows data **in a browser** | The smoke drives the API, not the browser. A Playwright smoke against the release install is a Later item of LUM-997. |
| **MS-PHONE** | A phone or laptop on the home Wi-Fi reaches the secure address, installs the app and uses notifications | Needs real phones and routers. One-time, LUM-331 Pass 8. |
| **MS-MIC** | Dictation with a real microphone in a phone browser | Needs a microphone and a phone. One-time, LUM-331 Pass 8 / LUM-1005. |
| **MS-007** | Meeting brief produces valid Markdown | N/A while the feature is not in the release. |

## N/A rule

Any skipped manual row must be marked **N/A** with a **one-line reason** in the release log. A smoke check that reports `na` carries its reason in the summary.

## Relationship to coverage matrices

Behaviours map to **MS-###** IDs and smoke check ids cited in [TEST-COVERAGE-MATRIX-core.md](testing/TEST-COVERAGE-MATRIX-core.md) and sibling matrices ([testing README](testing/README.md)).

**Hub code-signing** rows (`4.2.4`, `4.2.5` in the Hub matrix) remain **`MS-TBD pending LUM-408`** until a signing procedure is documented.

## Related docs

- [CONTRIBUTING.md](../CONTRIBUTING.md) — contributor testing and matrix maintenance
- [automated-test-strategy.md](testing/automated-test-strategy.md) — automated layers and RC gates
- [capabilities.md](capabilities.md) — GHCR images and the release install
- [deployment/upgrading.md](deployment/upgrading.md) — updating, pinning, going back
