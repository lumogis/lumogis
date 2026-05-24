# ADR-060: Pre-launch security audit methodology (LUM-190)

**Status:** Finalised  
**Created:** 2026-05-22  
**Last updated:** 2026-05-22  
**Decided by:** `/explore --headless LUM-190` (Claude Opus 4.7); implementation verified `/verify-plan --headless` **2026-05-22** (Composer)

## Context

Lumogis is preparing the public AGPL `0.1` / HN-launch milestone. The launch sequence routes security first: **security audit (LUM-190) → CLA → README → GitHub with good-first-issues**. The audit is explicitly **not a penetration test** — it is an internal review with a findings deliverable (structured doc under **`docs/security-audit/`** in this tree — plan text used `docs/security/`; see findings doc path note) covering auth (JWT/session/CSRF/revocation), document-injection sanitisation, credential storage, `/api/v1/*` auth-gating, the LUM-23 privacy bug closure, Docker compose exposure, and **`make audit-local`** (pip-audit + npm audit) with honest default failure semantics (any reported advisory can fail the gate unless tooling flags change).

Lumogis already ships extensive security-relevant infrastructure: `scripts/audit_local.sh`, `SECURITY.md` + ADR 044, ADRs 006, 017, 018/024/026/027, 029, 035, 039, 041/050, 049, LUM-43 compose-policy CI, LUM-94 OpenAPI drift CI, LUM-276 attestation workflows. The question was **how** to conduct LUM-190 — methodology choice, not new security architecture.

## Decision

Adopt **Option 2 — Hybrid manual checklist + lightweight automated SCA/SAST in CI**: produce the structured findings doc, wire **`make audit-local`** into a new path-gated **`security-audit`** CI job (see `.github/workflows/ci.yml`), add **Bandit** SAST as an advisory check on `orchestrator/` (`-ll -ii`, non-blocking for v0.1), and commit **OWASP ZAP baseline** JSON referenced from the findings doc (operator one-shot; passive baseline limitations documented). Reject Option 1 (manual-only — weak CI posture), Option 3 (external pentest — explicit non-goal), and Option 4 (tool-only — cannot assert Lumogis-specific authorisation invariants).

## Alternatives Considered

See draft `.cursor/adrs/LUM-190-pre-launch-security-audit.md` and `.cursor/explorations/LUM-190-pre-launch-security-audit.md` for full option analysis.

## Consequences

**Easier:**

- Path-gated CI runs **`make audit-local`** on PRs touching security-relevant paths (see `.github/scripts/security-audit-paths.sh` + contract tests `.github/scripts/test-security-audit-paths.sh`).
- v0.2+ inherits the audit gate; the findings doc is the seed for quarterly audits.
- ZAP baseline JSON commits passive-DAST evidence without Docker-in-Docker DAST in CI for v0.1.

**Harder:**

- Bandit false-positive triage (mitigated: advisory in v0.1).
- npm/pip advisory strictness may require coordinated dependency bumps (documented in findings + `CHANGELOG.md`).

**Future chunks must know:**

- Canonical written artefact: **`docs/security-audit/pre-launch-audit-2026.md`** (or **`docs/security/`** after optional `git mv` when host `docs/security` is not a root-owned mount).
- **`security-audit`** job is path-gated; Bandit is advisory; promote blocking only with a dedicated ticket.
- **LUM-31**, **LUM-279**, **LUM-296** consume or extend audit conclusions — register Linear **`blocks`** edges from **LUM-190** when Product OS approves (`/linear-update`).

## Status history

- 2026-05-22: Draft created by `/explore --headless LUM-190` (Claude Opus 4.7).
- 2026-05-22: Finalised by `/verify-plan --headless` — hybrid methodology implemented (CI + findings + ZAP JSON + `scripts/requirements-security-audit.txt` + `make bandit-check`); draft mirrored under `docs/decisions/060-lum-190-pre-launch-security-audit-methodology.md`.
