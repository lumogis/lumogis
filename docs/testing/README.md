# Testing documentation index

## Coverage matrices (feature → test evidence)

Lumogis maps **product behaviours** to automated tests (or manual smoke) in four matrices split by export boundary:

| Matrix | Path | Audience |
| --- | --- | --- |
| **Core** | [TEST-COVERAGE-MATRIX-core.md](TEST-COVERAGE-MATRIX-core.md) | Public AGPL tree |
| **Web** | [TEST-COVERAGE-MATRIX-web.md](TEST-COVERAGE-MATRIX-web.md) | Public AGPL tree |
| **KG** | `docs/private/testing/TEST-COVERAGE-MATRIX-kg.md` | Private checkout only (not in public AGPL export) |
| **Private appliance** | `docs/private/testing/TEST-COVERAGE-MATRIX-hub.md` | Private checkout only (not in public AGPL export) |

**Legend:** ✅ tested with cited assertion · 🟡 partial · ❌ gap · 🚫 manual (`MS-TBD` until [LUM-385](https://linear.app/lumogis/issue/LUM-385) manual smoke doc).

**v1 baseline:** **LUM-384** code audit + **LUM-428** strict ✅ rules and **active + archived plan** cross-check (`.cursor/plans/` and `archived/`). Re-seed: `python3 scripts/testing/_lum384_seed_matrices.py`. Citation audit: `python3 scripts/testing/_lum428_audit_matrix_citations.py`. Structure/ID gate: `make coverage-matrix-check` (or `node scripts/check-coverage-matrix.mjs`; catalog `scripts/feature-ids.json` — regenerate with `--write-catalog` after ID changes).

**Ongoing updates:** rows are added or revised when features close via **`/verify-plan` Step 7c (LUM-427)** — one row per shipped behaviour + test cite, not on every drive-by PR. Procedure: `.cursor/skills/verify-plan/SKILL.md` § Step 7c. See [CONTRIBUTING.md](../../CONTRIBUTING.md) § *Coverage matrices*.

**Related:**

- [automated-test-strategy.md](automated-test-strategy.md) — *how to run* test layers
- `scripts/debug/inventory.tsv` — command index (LUM-377); not a feature matrix

## Other testing docs

- [automated-test-strategy.md](automated-test-strategy.md) — layered commands, CI, release gates
