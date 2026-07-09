# ADR-147: Cloud LLM privacy mode — local-only enforcement at the orchestrator provider chokepoint (LUM-194)

**Status:** Finalised

**Created:** 2026-06-30

**Last updated:** 2026-06-30

**Decided by:** /explore (claude-opus-4-8-thinking-medium, headless run for LUM-194)

**Finalised by:** /verify-plan 2026-06-30 (Composer)

**Plan:** `.cursor/plans/archived/LUM-194-cloud-llm-privacy-mode.plan.md`

**Exploration:** `.cursor/explorations/archived/LUM-194-cloud-llm-privacy-mode.md`

**Draft mirror:** `.cursor/adrs/LUM-194-cloud-llm-privacy-mode.md`

**Linear:** [LUM-194](https://linear.app/lumogis/issue/LUM-194/cloud-llm-privacy-mode-local-only-toggle-hard-enforcement-per-user-and)

## Context

Lumogis positions itself as local-first and privacy-first, but no mechanism made that guarantee
*deterministic*: any operator/user with cloud LLM keys configured could route inference off-machine,
and the natural-language policy file (LUM-148) is advisory, not enforcement. LUM-194 requires a hard
"local-only" privacy mode where **remote** LLM routing through the provider factory is blocked when
enabled, defaulting to on for fresh installs, with an admin-controlled instance lock, per-user
further-restriction (users may restrict but not expand beyond the instance allowance), audit logging
of blocked attempts, and a plan-mode/complex-synthesis fallback to local Ollama (ADR-107 defers its
cloud-fallback gating here). **Routing policy only** — network egress isolation (`tethered`) is
defense-in-depth follow-up, not this slice.

The constraint that shaped the option space: enforcement must be **complete on the default
deployment path**. Investigation showed the LiteLLM proxy is *optional* and off the default path —
cloud routing normally hits provider `base_url`s directly via the orchestrator's adapters — so
proxy-level or pure-infrastructure enforcement would leave the primary route open.

## Decision

Enforce privacy mode in the **orchestrator's central LLM provider factory** —
`orchestrator/config.py::get_llm_provider` (and `is_model_enabled`) — the single seam every call
site already routes through (chat routes, signal processor, memory, entities, routines, mcp_write,
loop). When effective policy is local-only, any request for a **remote** model (not positively
local per `is_local_model()` — i.e. not Ollama/loopback `base_url`) is blocked with a typed
`PrivacyModeBlocked` before the remote adapter is constructed, remote models are hidden from
`/v1/models`, the attempt is recorded via the append-only audit substrate as `privacy_mode_block`
(frozen JSON schema with `decline_type: external_call_denied` inside `input_summary` for LUM-137),
and plan/complex flows fall back to a local Ollama model with a quality/latency warning rather than
silently to remote. Effective policy = instance `app_settings` lock + per-user further restriction;
fresh installs default to local-only and remote requires explicit opt-in that an instance lock can
forbid; upgrades with existing cloud usage seed `allow_cloud` via migration 043. An in-process egress
allowlist (`tethered`) is recorded as an *optional future defense-in-depth layer*, not part of this slice.

## Alternatives considered

- **In-process Python egress allowlist (`tethered`, PEP 578 audit hooks):** valuable defense-in-depth
  but alpha maturity / tiny adoption and no per-user-or-instance UX make it unsuitable as the sole
  mechanism — deferred as an opt-in later layer.
- **Docker `internal` network + Squid/nginx forward-proxy allowlist:** static, no runtime/per-user toggle,
  breaks opt-in cloud UX, and does not exist for native Core (Persona C). Ruled out.
- **LiteLLM-proxy-only enforcement:** LiteLLM is optional and off the default path. Ruled out.
- **OFFLINE_MODE-style boot env var only:** no per-user/instance hierarchy or runtime toggle. Rejected for v1.

## Consequences

- **Easier:** A single, testable guarantee that no **remote** LLM call is routed through
  `get_llm_provider` when local-only is on; reuses `app_settings`, audit, and `is_local_model`;
  no new Docker service; works on native Core.
- **Harder / foreclosed:** LUM-121 mode system and LUM-169 cloud tiers must respect the gate;
  LUM-148 policy text is advisory-only for cloud blocking; LUM-137 must consume `privacy_mode_block` /
  `external_call_denied` vocabulary.
- **Future chunks must know:** do not add cloud call paths that bypass `get_llm_provider`.

## Revisit conditions

- If `tethered` (or equivalent) reaches stable maturity, revisit in-process egress allowlist as defense-in-depth.
- If a future architecture introduces cloud call paths **outside** `get_llm_provider`, revisit gate placement.
- If LUM-121 formalises richer per-mode routing, revisit whether the gate moves into that system.

## Status history

- 2026-06-30: Draft created by /explore (headless, LUM-194).
- 2026-06-30: Revised during /review-plan --arbitrate R1 — fail-closed predicate, migration 043 upgrade seeding, frozen audit JSON, unified `lumogis.privacy` shape.
- 2026-06-30: Finalised by /verify-plan — implementation confirmed on `agent/lum-194`.
