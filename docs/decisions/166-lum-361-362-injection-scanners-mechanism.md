# ADR 166: User-config secrets + tool-result injection scanners

**Status:** Finalised
**Created:** 2026-07-14
**Last updated:** 2026-07-14
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-07-14 (Composer)
**Plan:** none — shipped in [PR #100](https://github.com/lumogis/lumogis-app/pull/100) before formal plan / verify cycle
**Exploration:** `.cursor/explorations/injection_scanners_mechanism_retro.md`
**Draft mirror:** `.cursor/adrs/injection_scanners_mechanism.md`
**Linear:** [LUM-361](https://linear.app/lumogis/issue/LUM-361), [LUM-362](https://linear.app/lumogis/issue/LUM-362)

## Context

LUM-127 (ADR-039) sanitises **ingested documents** at ingest time. LUM-361 and LUM-362 cover two additional injection/credential surfaces that bypass that path:

1. **User-authored Lumogis config** (`pipe.md`, procedures, `LUMOGIS_POLICY.md`, `WAKE.md`) — accidental credential embedding before LLM context injection.
2. **Live MCP tool results** — connector output injected inline during a session, never passing through the ingest pipeline.

PR #100 shipped the ports-and-adapters scanners; follow-up wiring (2026-07-14) connects LUM-362 to :func:`services.tools.run_tool` and exposes LUM-361 pre-load helpers for config loaders (LUM-110+).

## Decision

Adopt two sibling scanners mirroring the LUM-127 pattern:

### LUM-361 — Secrets scanner

- **`orchestrator/data/secret_patterns.yaml`** — 14 credential-detection patterns (13 enabled by default; UUID-format token opt-in via `enabled: false`).
- **`ports/secrets_scanner.py`** — `SecretsScanner` Protocol + `SecretsScanResult`.
- **`services/secrets_scanner.py`** — `PatternSecretsScanner`: regex scan, first hit per pattern, **never logs raw secret content**; `remediation_message()` for operator-facing errors.
- **`adapters/null_secrets_scanner.py`** — test/disabled passthrough.
- **`config.get_secrets_scanner()`** — `PatternSecretsScanner` when `SECRETS_SCANNER_ENABLED=true` (default); `NullSecretsScanner` when false.

On detection (when wired): block file load; show actionable error naming pattern + line; **do not** write secret content to audit log.

### LUM-362 — Tool-result injection scanner

- **`orchestrator/data/tool_result_patterns.yaml`** — tool-result-specific additions (`tr_*` ids).
- Reuses **`services/injection_sanitiser.load_pattern_rows_from_path`** over the **fixed** default `injection_patterns.yaml` baseline (not operator override path) plus additions — cross-file id collision fails loudly.
- **`ports/tool_result_scanner.py`** — `ToolResultScanner` Protocol.
- **`services/tool_result_scanner.py`** — `PatternToolResultScanner`: whole-result redaction on any hit; `scan_tool_result()` middleware entry point; `REDACTION_PLACEHOLDER` + `USER_NOTIFICATION` constants for callers.
- **`adapters/null_tool_result_scanner.py`** — passthrough.
- **`services/tool_result_guard.guard_tool_result`** — wired from :func:`services.tools.run_tool` (LUM-362 middleware).
- **`scan_user_config_for_llm` / `read_user_config_for_llm`** — mandatory pre-LLM entry points for user config (LUM-361).

### Wired (2026-07-14)

- LUM-362: every :func:`services.tools.run_tool` return passes through :func:`guard_tool_result` (audit + redact on flag).
- LUM-361: loaders must call :func:`scan_user_config_for_llm`; no config-file loader on ``dev`` yet (LUM-110+).

### Deferred

- Playwright / live-stack proof when connectors ship.
- Config loaders (LUM-110/353/148/234) must call :func:`scan_user_config_for_llm` when those surfaces ship.

## Alternatives considered

- **Single combined scanner** — rejected; secrets block-and-hold vs injection redact-and-audit are different failure modes and logging rules.
- **Duplicate LUM-127 loader** — rejected for tool-result path; shared loader reduces drift.
- **Ship wiring in same PR** — deferred; targets (config loaders, live connectors) not yet on `dev`.

## Consequences

- Future `/create-plan` work for LUM-110/353/148/234 must call `scan_user_config_for_llm` before LLM injection.
- All LLM-loop tool results pass through `guard_tool_result` via `run_tool`.
- Pattern YAML changes require unit test updates in `test_secrets_scanner.py` / `test_tool_result_scanner.py`.
- ReDoS mitigation: catastrophe lint + bounded regex on `env_secret_assignment` (review hardening in PR #100).

## Revisit conditions

- First live connector ships → wire LUM-362 middleware + matrix row; treat as P0.
- First user-config loader ships → wire LUM-361 + matrix row.
- LUM-141 safety playground → add adversarial cases for both scanners.

## Testing retrospective

| Item | Detail |
| --- | --- |
| **Added** | `test_secrets_scanner.py`, `test_tool_result_scanner.py`, `test_tool_result_guard.py` |
| **Run** | pytest on scanner + guard modules |
| **Gaps** | Config-loader integration when LUM-110 ships; live connector proof when connectors ship |

## Linear linkage (Product OS)

| Issue | Outcome |
| --- | --- |
| **LUM-361** | **Done** — scan API + patterns; config loaders call `scan_user_config_for_llm` (LUM-110+) |
| **LUM-362** | **Done** — `run_tool` middleware via `guard_tool_result` |

## Status history

- 2026-07-14: Finalised by `/record-retro` — mechanism in PR #100.
- 2026-07-14: LUM-362 wired in `run_tool`; LUM-361 pre-load API shipped.
- 2026-07-14: Coverage matrix rows **1.8.13** (LUM-361), **1.8.14** (LUM-362) added to `TEST-COVERAGE-MATRIX-core.md`.
