# ADR 172: Capability sandbox + egress — community tier gate (LUM-613)

**Status:** Finalised
**Created:** 2026-07-16
**Last updated:** 2026-07-16
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-07-16 (Composer)
**Plan:** none — shipped on `claude/lum-613-capability-sandbox-egress`
**Exploration:** `.cursor/explorations/lum_613_capability_sandbox_egress_retro.md`
**Draft mirror:** `.cursor/adrs/lum_613_capability_sandbox_egress.md`
**Linear:** [LUM-613](https://linear.app/lumogis/issue/LUM-613) (LUM-507 pillar b; parent LUM-507)

## Context

ADR-170 pillar **(b)** requires sandbox + egress constraints before opening a community capability ecosystem. OOP capabilities run in separate processes; Core's in-process `tethered.scope()` (ADR-153) cannot stop container egress. This chunk ships the **enforceable Core-side half**: trust classification, dispatch gating, registry hygiene, compose-policy proof, and in-process plugin refusal.

## Decision

### OOP-only for untrusted code (ADR-170 §0)

`load_plugins()` refuses non-first-party modules before import (`orchestrator/plugins/__init__.py`, `capability_egress.assert_first_party_plugin`).

### Origin-pinned first-party trust

Operator-maintained `orchestrator/first_party_capabilities.txt` maps **`capability_id → expected_base_url`**. A capability is **community (untrusted)** unless both id and discovery origin match the allowlist (`services/capability_egress.py`).

### Fail-closed community dispatch

Community capabilities are **refused dispatch** (structured `tool-unavailable`, hidden from LLM catalog) unless operator sets **`LUMOGIS_ALLOW_UNCONTAINED_COMMUNITY_CAPABILITIES=true`** (`config.py`, `unified_tools.py`). Honest copy: hard network containment is **LUM-618**, not this gate alone.

### Registry + manifest egress declaration

- `external_endpoints` validated on manifest (`models/capability.py`).
- Registry refuses base_url drift for existing ids; evicts on content validation failure (`capability_registry.py`).
- Community services must not receive Core DB creds — `docker-compose.test-policy-community-capability.yml` + `make compose-policy-check-community`.

### Static hygiene

`scripts/check_no_tethered_lum613.py` ensures capability modules do not import tethered at module level (CI).

## Alternatives considered

- Mandatory in-Python `tethered.scope()` around OOP invoke only — insufficient alone (ADR-153 ceiling); deferred DiD → LUM-619.
- Trust by `license_mode` manifest field — rejected; author-controlled, not operator trust.

## Consequences

- Sandboxed **unsigned community tier** is architecturally defined; marketplace public launch still gated on **LUM-618**.
- LUM-614 signing can add verified tier without rewriting this gate.

## Revisit conditions

- **LUM-618** Done → container network policy becomes primary egress guarantee.
- **LUM-614** Done → cryptographic identity replaces origin-pinned allowlist for verified tier.

## Linear linkage (Product OS)

- **Shipped under:** LUM-613 (Done after merge @ `8b979bfc8`)
- **Hard containment follow-up:** LUM-618 (marketplace launch gate)
- **P2:** LUM-617, LUM-619, LUM-620

## Testing retrospective

- **Tests:** `test_capability_egress.py`, `test_plugin_trust_guard.py`; matrix row **1.6.12**
- **Verified:** 53 pytest passed; `make coverage-matrix-check` green; `compose-policy-check-community` green; `check_no_tethered_lum613` clean (2026-07-16)
- **Gap:** container socket refusal proof → LUM-618

## Status history

- **2026-07-16:** Finalised by /record-retro (as-shipped on `claude/lum-613` → `dev`).
