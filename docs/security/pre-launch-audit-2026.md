# Pre-launch security audit — hybrid methodology (LUM-190)

**Linear:** [LUM-190](https://linear.app/lumogis/issue/LUM-190/security-audit-pre-launch-review-of-auth-injection-credentials-api)  
**Audit type:** Internal pre-launch review (not external penetration test).  
**Methodology:** Manual checklist + evidence from existing ADRs/tests + **`make audit-local`** (pip-audit + npm audit, any reported advisory fails by default) + advisory **Bandit** + one-shot **OWASP ZAP baseline** JSON.

## Path note (repository layout)

Canonical location per LUM-190 plan: **`docs/security/pre-launch-audit-2026.md`**. Passive ZAP JSON remains at **`docs/security-audit/zap-baseline-2026.json`**.

## OWASP ZAP baseline — reproducibility header

| Field | Value |
| --- | --- |
| **Container image** | `ghcr.io/zaproxy/zaproxy@sha256:8770b23f9e8b49038f413cb2b10c58c901e5b6717be221a22b1bcab5c9771b8a` (pinned digest; tag `stable` at pull time) |
| **ZAP program version (JSON `@version`)** | `2.17.0` |
| **Baseline script** | `zap-baseline.py` (bundled in image; `-I` so warnings do not fail exit code for smoke capture) |
| **Scan date (UTC)** | `2026-07-09T09:26:24Z` (from JSON `created`) |
| **Target base URL** | `http://127.0.0.1/` |
| **Auth mode** | **`none`** (unauthenticated passive baseline) |
| **Alert counts (risk)** | **FAIL:** 0, **WARN:** 4, **INFO:** 0 (from scan stdout summary) |
| **Committed JSON** | [`zap-baseline-2026.json`](../security-audit/zap-baseline-2026.json) |
| **Reproduce (RC stack)** | **`make zap-rc-baseline-lum318`** (`scripts/zap-rc-baseline-lum318.sh`) — requires public-RC Compose listening on **`127.0.0.1:80`** |

**Limitation (mandatory):** Passive baseline against the RC URL at **`http://127.0.0.1/`** exercises **header / cache / CSP** style findings only. **Authenticated** posture for **`/api/v1/*`**, **`/mcp/*`**, and **`/graph/*`** is evidenced by **this document’s tables**, **cited ADRs**, and **existing integration tests** (`tests/integration/test_public_rc_*`, `test_caddy_security_headers.py`), not by the baseline JSON alone. Operators preparing a real RC cut should re-run **`make zap-rc-baseline-lum318`** (or the `docker run` shape in **`Makefile`** **`bandit-check`** comments) against the **RC base URL** and replace the JSON + update this header.

## Master findings table

| Finding ID | Theme | Status | Evidence | Notes | revisit-by | Reviewer / date |
| --- | --- | --- | --- | --- | --- | --- |
| AUTH-001 | Auth (JWT / sessions / revocation) | MITIGATED | ADR 041, ADR 050; `orchestrator/auth.py`, `services/auth_sessions.py`, `routes/auth.py`; `tests/integration/test_public_rc_auth_session.py` | Multi-device refresh + `tv` invalidation + optional `sid` lookup per ADR 050 / LUM-243. | — | Composer / 2026-05-22 |
| INJ-001 | Injection (documents / tool chain) | MITIGATED | ADR 039; `docs/decisions/039-*`; LUM-127 | Sanitiser + `TOOL_CHAIN_CAP`; not re-audited line-by-line in this pass. | — | Composer / 2026-05-22 |
| CRED-001 | Credentials (per-user / scopes) | MITIGATED | ADR 018, 024, 026, 027, 029; `SECURITY.md` | Household + connector boundaries per credential ADRs. | — | Composer / 2026-05-22 |
| API-001 | API surface / auth gating | MITIGATED | OpenAPI / `authz.require_user` on v1 routers; `tests/integration/test_public_rc_negative_integration.py` | Representative routes use `Depends(require_user)` (e.g. `routes/api_v1/*`, MCP token routes). | — | Composer / 2026-05-22 |
| DEPCVE-001 | Dependency / CVE posture (SCA) | MITIGATED | `scripts/audit_local.sh`; CI job **`security-audit`**; `make audit-local`; `clients/lumogis-web` `npm audit` (2026-07-02) | **npm audit** and **pip-audit** fail on **any** reported vulnerability unless tooling flags are changed (see plan error table — no implied High/Critical-only gate). **lumogis-web** dev-deps refreshed **2026-07-02** (`npm audit fix`, no `--force`): form-data (jsdom chain), vite, js-yaml, @babel/core — **0** advisories; `npm audit --omit=dev` remains **0**. **Dev-dependency policy (DEPCVE-001):** advisories in devDependencies with no production-bundle exposure — apply non-breaking `npm audit fix` where available; otherwise **ACCEPTED** with revisit-by date. | — | Composer / 2026-07-02 |
| SAST-001 | Bandit (orchestrator + graph) | ACCEPTED | CI advisory steps; `make bandit-check` | Non-blocking in v0.1; elevation tracked as follow-up child in plan register. | 2026-08-22 | Composer / 2026-05-22 |
| COMP-001 | Docker / compose exposure | MITIGATED | LUM-43; `make compose-policy-check*`; ADR 010/011 capability manifests | CI **`compose-policy`** job. | — | Composer / 2026-05-22 |
| CSRF-001 | CSRF / SameSite / Origin | ACCEPTED | LUM-31; cookie/session design in auth routes + ADR 041 | Audit does not change SameSite posture; **LUM-31** remains the closure vehicle if double-submit CSRF is required beyond current browser defaults. | 2026-08-22 | Composer / 2026-05-22 |
| MCP-001 | MCP bearer posture | ACCEPTED | ADR 017; `routes/mcp_*`; LUM-296 | Current behaviour documented vs ADR 017; **streamable HTTP MCP hardening** stays **LUM-296** unless a P0 is opened as a new child. | 2026-08-22 | Composer / 2026-05-22 |
| ISO-001 | LUM-23-class user isolation | MITIGATED | LUM-23 / FP-042; `services/lumogis-graph/tests/test_graph_stats_privacy.py`; ADR 035 | Graph stats privacy regression tests; see **LUM-23 procedure** below. | — | Composer / 2026-05-22 |
| ISO-002 | Qdrant LAN exposure (unauthenticated vector store) | MITIGATED | LUM-565; `docker-compose.yml` (`127.0.0.1:${QDRANT_HOST_PORT:-6334}:6333`); ADR 053; `QDRANT_URL=http://qdrant:6333` (container network) | Default compose publish bound all interfaces; Qdrant HTTP has no auth and holds embedded personal document content. Loopback-only host bind per ADR-053 FalkorDB precedent; orchestrator reachability unchanged. | — | Composer 2 / 2026-07-02 |
| DAST-001 | Passive DAST (ZAP baseline) | MITIGATED | `zap-baseline-2026.json`; ZAP header above; **`make zap-rc-baseline-lum318`** (**LUM-318**) | Passive baseline against RC URL `http://127.0.0.1/` (auth none); refreshed **2026-07-09** — see ZAP header. | — | Composer / 2026-07-09 |

## LUM-23-class user isolation — procedure (required)

1. **Security-relevant route classes:** `/api/v1/*` (FastAPI routers under `orchestrator/routes/api_v1/`), MCP (`mcp` / token routes), graph proxy (`/graph/*` and KG service when enabled). Evidence: OpenAPI snapshot / router `dependencies=[Depends(require_user)]` patterns.
2. **DB / index / graph touchpoints:** Core uses SQLAlchemy-style services; user scoping is enforced in service layers and visibility helpers (see ADR 051 context building, Qdrant filters in ADR-related code). **Short verification table:**

| Call site / area | User filter present | Evidence |
| --- | --- | --- |
| API v1 routers (documents, memory, KG API surface) | Y (bearer + `require_user`) | `grep` `require_user` in `orchestrator/routes/api_v1/` |
| Graph stats (service mode) | Y | `services/lumogis-graph/tests/test_graph_stats_privacy.py` |
| Qdrant document search | Y | ADR 039 / visibility filter pipeline (orchestrator adapters) |

3. **Automated tests cited:** `tests/integration/test_public_rc_auth_session.py`, `tests/integration/test_public_rc_negative_integration.py`, `tests/integration/test_caddy_security_headers.py`, **`test_graph_stats_privacy.py`** (LUM-23).

## Minimum implementer secret-pattern scan (sign-off)

Run from repository root (no hits expected in source for these illustrative patterns):

```bash
grep -R "lin_api_" orchestrator/ clients/lumogis-web/src/ 2>/dev/null || true
grep -R "Bearer lin_" orchestrator/ clients/lumogis-web/src/ 2>/dev/null || true
```

**Outcome (2026-05-22):** no unintended matches in `orchestrator/` or `clients/lumogis-web/src/` for the patterns above.

## Export / public tree hygiene

- **`docs/security-audit/`** is **not** listed in `scripts/public-export-strip-list.txt` (verified by search).
- `scripts/check-public-export.sh` enforces: paths on the strip list are **absent** from an export candidate, plus licence / dotenv rules — read that script before assuming “not stripped” implies “present in export”; export layout is defined by the export scripts, not this audit alone.

## Out of scope (v0.1)

- External penetration test (explicit non-goal on LUM-190).
- CodeQL, Snyk, Semgrep, Trivy, gitleaks/trufflehog automation (follow-up children per plan **Deferred** register).
- Authenticated ZAP spider against live sessions (follow-up; JSON redaction policy still applies).

## Operator sign-off

| Role | Name | Date | Notes |
| --- | --- | --- | --- |
| Primary reviewer | Thomas (pending) | — | Fill at merge / release prep. |
| Second reviewer | *Recommended* | — | If solo, record explicit risk acceptance in Linear comment on **LUM-190**. |

## Linear / Product OS follow-ups

- Register **`blocks`** edges **LUM-190 → LUM-31, LUM-279, LUM-296** when approved (`/linear-update` or manual GraphQL) — see plan **Follow-up register**.
- **`docs/README.md`** cross-link to this folder: deferred per overlap note vs `origin/cursor/documentation-collection-maintenance-671b`; this file is linked from **`CHANGELOG.md`** instead.
