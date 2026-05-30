# ADR-065: Lumogis doctor v2 — shell `--fix` remediation (slice 1)

**Status:** Finalised
**Created:** 2026-05-24
**Finalised:** 2026-05-24 (`/verify-plan` — **LUM-320**, headless agent branch)
**Decided by:** Exploration + dual-round plan arbitration; implementation verified against `LUM-320-doctor-v2-fix-remediation.plan.md`. Parent context: **ADR-061** (v1 read-only doctor).

## Context

`make doctor` v1 (ADR-061) intentionally shipped read-only because v1's load-bearing constraint is *"works even when the orchestrator is down."* LUM-320 implements the deferred **`--fix`** slice: host-level stack repairs with audit, without orchestrator imports. The Linear issue carries `risk:data-loss` and `risk:security` labels: mutations must stay on a narrow safelist with NDJSON audit and `.env` value redaction (≥8 characters).

## Decision

Doctor v2 extends the **shell** doctor with **`--fix`**. Detection (v1 behaviour) is unchanged. Eligible repairs are emitted as **7-field TSV** rows (`fix_kind` + `fix_target` JSON). **`repair.sh`** executes only **`compose_up_service`**, **`ollama_pull_model`**, and **`mkdir_backup_dir`** after **`S ∩ K`** validation, model regex + `ollama pull --` argv hardening, and **`mkdir_backup_dir`** path policy (repo or fixed host roots; parent directory must exist). **`--fix`** defaults to **dry-run**; **`--fix --apply --yes`** performs mutations. **`--dry-run` wins** over **`--apply`** when both appear. **`--json --fix`** emits **`version: 2`** with **`apply_requested`**, **`any_applied`**, **`dry_run`** (`dry_run := !apply_requested` in slice-1 emitters), and **`repairs[]`**; plain **`--json`** without **`--fix`** stays **`version: 1`**. **`DOCTOR_SECURITY=1` / `--security`** refuses **`--fix --apply`** with **exit `4`**. Non-interactive **`--fix --apply`** without **`--yes`** refuses with **exit `4`**; with **`--json`**, stdout is **empty** on refusal.

## Alternatives Considered

- **In-process `python -m orchestrator.doctor --fix`** — rejected: contradicts the v1 "orchestrator may be down" invariant; LUM-322 retains **probes only**.
- **Standalone `make fix-*` targets as the v2 architecture** — rejected: loses detection→repair linkage and structured audit.
- **Broad safelist including `.env` writes in slice 1** — rejected: requires separate ADR before config mutation.

## Consequences

- Single operator entrypoint for detection **and** slice-1 repairs; **`schema.v2.json`** is the machine contract for **`--json --fix`**; LUM-178 must validate v1 vs v2 separately.
- v1 invariant preserved: doctor still runs when the orchestrator container will not boot.
- LUM-322 scope must remain **in-process probes only — not remediation** after this ships.
- New testing surface: `orchestrator/tests/test_doctor_cli.py` covers v2 JSON, apply, refusal, audit redaction, and path policy (additional cases remain deferred; see plan follow-up register).
- **LUM-338 (2026-05-29):** Closes the deferred **audit NDJSON rotation** gap — in-process size cap + generation retention on the **`repair.sh`** write path (`LUMOGIS_DOCTOR_AUDIT_MAX_BYTES` / `LUMOGIS_DOCTOR_AUDIT_MAX_FILES`); policy recorded in **ADR-061** amendment and **`scripts/doctor/README.md`**. Concurrent **`--fix --apply`** locking remains a separate LUM-320 follow-up.

## Revisit conditions

- A safelist entry shows non-idempotent or unsafe behaviour in operator reports → revisit before expanding **`K`** or repair kinds.
- Slice 2 (`.env` or config-edit safelist) → mandatory new ADR amendment with threat model + rollback story.
- LUM-178 "apply fix" from non-CLI surfaces → revisit **`--apply`** exposure and blast-radius UX.
- **`mkdir_backup_dir`** needs roots outside the fixed list → revisit slice 1 or move to config tooling.

## Status history

- 2026-05-24: Draft created by `/explore --headless` (LUM-320).
- 2026-05-24: Revised during `/review-plan --arbitrate` R1–R2 (JSON triad, **`command_argv`**, security refusal, exit band, compose state gate).
- 2026-05-24: Finalised by `/verify-plan --headless` — implementation confirmed; canonical copy here (draft mirror remains under `.cursor/adrs/LUM-320-doctor-v2-fix-remediation.md` until devtools sync).
- 2026-05-29: **LUM-338** shipped audit log rotation — pointer only; see **ADR-061** amendment (audit retention bullet).
