# ADR-065: Lumogis doctor v2 — shell `--fix` remediation (slice 1)

**Status:** Finalised
**Created:** 2026-05-24
**Finalised:** 2026-05-24 (`/verify-plan` — **LUM-320**, headless agent branch)
**Decided by:** Exploration + dual-round plan arbitration; implementation verified against `LUM-320-doctor-v2-fix-remediation.plan.md`. Parent context: **ADR-061** (v1 read-only doctor).

## Context

`make doctor` v1 (ADR-061) intentionally shipped read-only because v1's load-bearing constraint is *"works even when the orchestrator is down."* LUM-320 implements the deferred **`--fix`** slice: host-level stack repairs with audit, without orchestrator imports. The Linear issue carries `risk:data-loss` and `risk:security` labels: mutations must stay on a narrow safelist with NDJSON audit and `.env` value redaction (≥8 characters).

## Decision

Doctor v2 extends the **shell** doctor with **`--fix`**. Detection (v1 behaviour) is unchanged. Eligible repairs are emitted as **7-field TSV** rows (`fix_kind` + `fix_target` JSON). **`repair.sh`** executes only **`compose_up_service`**, **`compose_restart_service`** (**LUM-342**), **`ollama_pull_model`**, and **`mkdir_backup_dir`** after **`S ∩ K`** validation, model regex + `ollama pull --` argv hardening, and **`mkdir_backup_dir`** path policy (repo or fixed host roots; parent directory must exist). **`--fix`** defaults to **dry-run**; **`--fix --apply --yes`** performs mutations. **`--dry-run` wins** over **`--apply`** when both appear. **`--json --fix`** emits **`version: 2`** with **`apply_requested`**, **`any_applied`**, **`dry_run`** (`dry_run := !apply_requested` in slice-1 emitters), and **`repairs[]`**; plain **`--json`** without **`--fix`** stays **`version: 1`**. **`DOCTOR_SECURITY=1` / `--security`** refuses **`--fix --apply`** with **exit `4`**. Non-interactive **`--fix --apply`** without **`--yes`** refuses with **exit `4`**; with **`--json`**, stdout is **empty** on refusal.

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
- 2026-06-15: **LUM-342** pointer — fourth repair kind **`compose_restart_service`** for unhealthy running containers; slice-1 behaviour unchanged.
- 2026-06-19: **LUM-341** — slice-2 `.env` config-edit safelist: threat model + decision + rollback amendment added below **and implemented** (append-only `set_env_key` repair kind behind `LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1`). Satisfies the slice-1 Revisit condition.

## Amendment — slice 2: `.env` config-edit safelist (2026-06-19, LUM-341)

**Status of this amendment:** Design accepted **and implemented** — `repair.sh` ships the `set_env_key` repair kind (append-only, opt-in via `LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1`), with manifest `scripts/doctor/env-safelist.json`, `config.sh` detection, `schema.v2.json` target shape, README, and tests in `orchestrator/tests/test_doctor_cli.py`. Implementation plan: `lumogis-devtools/cursor/plans/LUM-341-doctor-env-safelist.plan.md`.

### Why this is needed

Slice 1 (ADR-065 above) deliberately refuses any `.env` mutation. Operators hit real cases where a **missing** non-secret key blocks a clean start or degrades a feature (e.g. `EMBEDDING_MODEL`, `LUMOGIS_DEFAULT_LLM`, `GRAPH_MODE`, `ORCHESTRATOR_HOST_PORT`, `COMPOSE_PROFILES`, feature flags). The ask is a **narrow, opt-in** ability for doctor to add such a key with a known-safe default — without ever endangering secrets or existing config.

### Threat model

`.env` is the highest-blast-radius file doctor could touch: it holds secrets and is load-bearing for the whole stack. Identified hazards:

1. **Secret destruction (`risk:data-loss`).** Overwriting `POSTGRES_PASSWORD`, DEK/JWT material, or any credential breaks decryption of existing data → permanent, unrecoverable loss.
2. **Secret leak (`risk:agpl` / public–private boundary).** Writing a secret value into `.env` and thence into the NDJSON audit, or into any tracked/exported tree, would breach export hygiene.
3. **Lockout / startup break.** A wrong value (bad port, malformed model name) bricks the stack.
4. **Comment/format destruction.** A naive rewrite drops operator comments, ordering, `export ` prefixes, and quoting.
5. **Concurrent edit / TOCTOU.** Operator editing `.env` while doctor writes → lost update.
6. **Quoting / injection.** Values with spaces, `$`, or quotes mishandled → surprises when `.env` is sourced.
7. **Multi-file ambiguity.** `.env.local`, overrides, and compose `env_file` lists — which file is authoritative?

### Decision (deny-by-default, append-only, non-secret)

- **Append-only.** Doctor may only **add a missing key**. If the key already exists (even commented), **refuse** (`outcome: skipped`). Existing lines are **never** rewritten, reordered, or deleted — this neutralises hazards 1, 4, and 5.
- **Versioned value safelist.** Editable keys come from a versioned manifest `scripts/doctor/env-safelist.json` (`{ "version": 1, "keys": { "EMBEDDING_MODEL": {...}, ... } }`), mirroring the LUM-340 `core-services.json` pattern (built-in fallback; malformed manifest can never widen the set). A key not in the manifest is **never** written.
- **Hard secret denylist (belt and braces).** Independent of the manifest, any key whose **name** matches a secret-shaped pattern (`*_PASSWORD`, `*_SECRET`, `*_KEY`, `*_TOKEN`, `*_DSN`, `DEK*`, `JWT*`, `*_CREDENTIALS`) is **hard-refused**, so a future manifest typo cannot expose a secret. Doctor **never generates** secrets.
- **Value validation.** The appended value is either a fixed default from the manifest or a doctor-inferred value already validated elsewhere (e.g. a model name passing `MODEL_RE`); values are written single-line, quoted only if needed, never containing a raw newline or TAB.
- **Single file.** Only `${LUMOGIS_REPO_ROOT}/.env`. `.env.local` / overrides / compose `env_file` chains are explicitly **out of scope**.
- **Atomic write + backup (rollback).** Before appending, copy `.env` → `.env.bak-<UTC-ts>` (mode `0600`); append via temp-file + `os.replace`. The backup path is printed and audited. Rollback = restore the backup, or delete the trailing doctor-added lines (append-only makes this trivial).
- **Dedicated opt-in gate.** `.env` edits are **off even under `--fix --apply`** unless `LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1` is also set — config mutation is higher blast-radius than starting a container, so it requires a second, explicit switch on top of all slice-1 gates (`--yes` / TTY / `flock` / `DOCTOR_SECURITY` refusal).
- **New repair kind.** `set_env_key` with `target = {"key","value"}`; `schema.v2.json` gains a fourth `target` `oneOf` shape. Audited like every other kind; the existing ≥8-char redaction pass still runs (defence in depth, even though values are non-secret by construction).
- **Export hygiene.** `.env` stays gitignored; neither the manifest nor the code carries secrets; doctor must **never** write to a tracked file. No new tracked-secret surface → AGPL/public-export posture unchanged.

### Rollback story

Every applied `set_env_key` leaves a `.env.bak-<ts>` (referenced in the repair row and audit NDJSON). To undo: `mv .env.bak-<ts> .env`, or open `.env` and delete the trailing `# added by lumogis doctor (LUM-341)` lines. Because edits are append-only and never touch existing values, no operator annotation or secret is ever at risk.

### Revisit conditions

- A safelist key proves unsafe to auto-add in operator reports → remove it from the manifest before expanding.
- Demand for **modifying** existing values (not just adding missing ones) → requires a further ADR amendment; append-only is a hard boundary here.
- `.env.local` / multi-file resolution becomes necessary → separate design, not this slice.
