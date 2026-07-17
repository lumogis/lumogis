# ADR-163 — Connector risk profiling: static per-connector risk profile + capability-derived floor (LUM-355)

**Status:** Draft (exploration) — recommendation for review, not yet implemented
**Created:** 2026-07-14
**Decided by:** `/explore` (Opus 4.8), three parallel codebase surveys + AgentShield/ECC prior-art review
**Linear:** [LUM-355](https://linear.app/lumogis/issue/LUM-355) (project: MCP & Tool Catalog; milestone v1.5; Urgent)
**Builds on:** ADR-054 (paperless connector), ADR-153 (egress allowlist), ADR-046 (zero-telemetry)
**Consumers / integration:** LUM-151 (trust ladder — activation gate), LUM-131 (per-action classifier — action gate), LUM-141 (safety playground), LUM-333 (connector manager UI), LUM-507 (plugin signing)
**Prior art:** ECC AgentShield — MCP-server risk profiling (capability scope + write-paths to sensitive external systems + operational risk classification), one of 5 scan categories / 102 static rules.

---

## Context

Lumogis is building proprietary connectors (email, calendar, photos, git) on top of the ones that already ship (paperless, caldav, ntfy). Today **all connectors are treated equally at activation**: a calendar read and an email send get the same handling. LUM-355 asks for a **static, per-connector risk profile** that (1) determines the trust tier required to activate a connector, (2) determines which actions require Ask vs auto-execute, and (3) flags connectors that route personal data to external services.

AgentShield (ECC) is the cited prior art: it *profiles* each MCP server by capability scope and write-paths and emits an A–F risk classification. The key difference for Lumogis: AgentShield is a **reactive scanner**; LUM-355 wants a **proactive profile declared at registration** that actively gates activation and execution.

### What the code survey found (three parallel surveys of `lumogis-app`)

**A connector registration seam already exists — attach, don't create.** `ConnectorSpec` / `CONNECTORS` in `orchestrator/connectors/registry.py:171-240`; its docstring explicitly sanctions additive fields (`field(default=...)`). But **there are two connector-id namespaces**: the credential registry (`ConnectorSpec`: `paperless`, `caldav`, `ntfy`, `llm_*`) and the tool registry (`ToolSpec` in `models/tool_spec.py:13`, `TOOL_SPECS` in `services/tools.py:326`: `filesystem-mcp`, `lumogis-memory`, plugin/capability connectors). They do **not** share ids, and OOP capability tools synthesise a third id form `capability.{id}` (`services/unified_tools.py:197`). A profile bolted onto `ConnectorSpec` alone would miss tool-bearing and capability connectors.

**The action-approval chokepoint exists and is clean.** `permissions.check_permission(connector, action_type, is_write, user_id)` (`orchestrator/permissions.py:97`) is the sole gate, fanned into by `actions/executor.py:74` and `services/tools.py:444` ("Permission bypass is not possible — every call goes through check_permission()"). Today it consults **only** the per-connector `ASK`/`DO` mode + `is_write` — there is **no per-action list**. `_HARD_LIMITED` (`routes/actions.py:29`: `financial_transaction, mass_communication, permanent_deletion, first_contact, code_commit`) is the existing hard floor that can never auto-elevate.

**The trust ladder (LUM-151) does not exist.** Zero `trust`/`tier`/`observer`/`delegate` state in `orchestrator/**`. `requires_trust_tier` has nothing to gate against — the activation gate must be built, and it depends on LUM-151.

**An action-risk vocabulary already exists but is UI-only.** `services/api_v1_risk.py:64` `risk_tier_for(action_type) → low|medium|high|hard_limit`, explicitly "a client-only UI cue … not a security gate". It is keyed on *action-type*, not connector.

**The audit log has a single choke.** `write_audit` (`actions/audit.py:44`) is the one INSERT; the connector-action path funnels through `executor._write_audit_and_fire` (`executor.py:114`), which already holds `spec.connector` — the natural place to inject `external_endpoints`.

**Zero-telemetry is a defended invariant** (ADR-046, `TELEMETRY.md`). There is no telemetry pipeline. So "connector activation events include risk tier" (the ticket's TELEMETRY.md integration point) can **only** mean enriching the **local, append-only audit log** — never shipping off-box.

**`accesses_pii` has no existing signal**; `writes_externally` has only a partial one (`spec.is_write` captures "mutates state", not "sends off-box"). Both are net-new static declarations. Egress today is gated only by the opt-in LLM-scoped tethered guard (ADR-153) and SSRF IP-range validation at connector-config time (`services/outbound_http_url.py:84`) — there is no per-connector declared-domain allowlist.

---

## Decision (recommended)

Adopt a **dedicated connector-risk registry** (not a field bolted onto one of the two existing registries), a **capability-derived risk floor** that a self-declared profile can never undercut, and **three integration arms sequenced by readiness** — two shippable now, one blocked on LUM-151.

### 1. `ConnectorRiskProfile` — refined from the ticket schema

Keep the ticket's dataclass, with three refinements (marked ▸):

```python
@dataclass(frozen=True)
class ConnectorRiskProfile:
    connector_id: str
    name: str
    data_sensitivity: Literal["low", "medium", "high", "critical"]
    writes_externally: bool
    accesses_pii: bool
    requires_trust_tier: str          # LUM-151 tier id (enum lands with LUM-151)
    auto_execute_actions: tuple[str, ...]   # ▸ tuple (frozen), not list
    always_ask_actions: tuple[str, ...]
    external_endpoints: tuple[str, ...]     # declared domains → audit + future egress
    declared: bool = True             # ▸ True = self-declared (needs backing); see §2
```

▸ `requires_trust_tier` is an **explicit** field, not derived from `data_sensitivity` — the ticket's own tables show sensitivity doesn't uniquely fix the tier (email-read `high`→collaborator, caldav-read `high`→assistant; `writes_externally` also matters). Keep it explicit, validate it for consistency against a `(sensitivity, writes_externally) → min-tier` table.

### 2. Dedicated registry + capability-derived floor (the core decision)

Put profiles in a **standalone `orchestrator/connectors/risk.py`**: `CONNECTOR_RISK_PROFILES: dict[str, ConnectorRiskProfile]` keyed by connector id, spanning **all** namespaces (credential, tool, `capability.*`), with a single resolver:

```python
def risk_profile_for(connector_id: str) -> ConnectorRiskProfile: ...
```

Two non-negotiable properties:

- **Default-deny fallback.** An unknown/unprofiled connector id resolves to the **maximum-risk** profile (`critical`, `requires_trust_tier=delegate`, every action `always_ask`, `writes_externally=True`). A connector nobody profiled is treated as maximally dangerous — the same posture as ADR-162. Critical for the LUM-507 plugin ecosystem.
- **Capability-derived floor overrides a too-low declaration.** A self-declared profile is a claim to *lower* a gate, and must be **backed**. The resolver raises the declared profile to a floor computed from *observable* capability:
  - any registered `ToolSpec` for the connector with `is_write=True` → force `writes_externally`/`data_sensitivity ≥ high` (`is_write` at `tool_spec.py:13`);
  - any MCP tool with `openWorldHint=True` (`mcp_server.py:_write_annotations`) → force `writes_externally=True`;
  - an action in `_HARD_LIMITED` → that action is forced into `always_ask_actions` regardless of declaration.

  This is the AgentShield lesson made active: **never trust a self-declared lower risk — derive a floor from what the connector can observably do.** First-party in-tree profiles are trusted by code review; **plugin profiles (LUM-507) must be signed *and* floored** by this derivation.

Profiles are **static code** (like `TOOL_SPECS`, `api_v1_risk._TIER_MAP`) for first-party connectors; plugin connectors carry their profile in the signed plugin manifest (LUM-507). No per-connector DB table for the profile itself — it is global/static; per-user activation state already lives in `connector_permissions` and `user_connector_credentials`.

### 3. Three integration arms, sequenced by readiness

**Arm A — action gate (`always_ask_actions` / `auto_execute_actions`) — SHIPPABLE NOW.**
Consume the resolved profile *inside* the existing chokepoint `permissions.check_permission` (`permissions.py:97`), before the `if is_write and mode == "ASK"` test: an action in `always_ask_actions` forces approval **even in DO mode**; one in `auto_execute_actions` may bypass the ASK block for genuine reads. Precedence: **`_HARD_LIMITED` > `always_ask_actions` > connector mode > `auto_execute_actions`** — the hard floor always wins. Highest value, no new gate, no dependency.

**Arm B — audit enrichment (`external_endpoints` + connector risk tier) — SHIPPABLE NOW.**
Inject `risk_profile_for(spec.connector).external_endpoints` and `data_sensitivity` at `executor._write_audit_and_fire` (`executor.py:114`); add a field to `AuditEntry` (`models/actions.py:42`) and a column to `audit_log` (`init.sql:328`, new migration) written by `write_audit`. **Local audit only** — this is the honest reading of the TELEMETRY.md integration point under the zero-telemetry invariant. `external_endpoints` can *also* seed the ADR-153 egress allowlist (`LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST`) as a follow-up, closing "which domains may this connector reach."

**Arm C — activation gate (`requires_trust_tier`) — BLOCKED on LUM-151.**
The trust ladder does not exist. Recommended sequencing: **interim coarse gate now** — activation of a `critical`/`delegate` connector (storing its credential via `PUT /api/v1/me/connector-credentials/{connector}`) requires explicit admin confirmation and defaults its permission mode to `ASK`; **full tier-ladder gate lands with LUM-151**, which reads `requires_trust_tier` from the profile at activation. The profile is authored now so LUM-151 has data to gate on the day it ships.

### 4. Surfacing to the UI (LUM-333)

The resolved profile surfaces through the existing aggregate read model `unified_tools.ToolCatalogEntry` (`unified_tools.py:66`, already carries `connector`, `action_type`, `is_write`, `permission_mode`) → the `GET /api/v1/me/tools` façade → the LUM-333 connector manager panel. Reconcile with `api_v1_risk.RiskTier`: the two are **orthogonal axes** — connector `data_sensitivity` × action `risk_tier_for(action_type)` — composed at display and at the gate, not duplicated into a third vocabulary.

---

## Alternatives considered

- **Bolt the profile onto `ConnectorSpec` only** — rejected: misses the tool-registry (`ToolSpec`) and `capability.*` namespaces; the two id-spaces don't share keys. A dedicated resolver keyed by connector id covers all three.
- **Put it on `ToolSpec`** — wrong granularity: risk is per-connector, `ToolSpec` is per-tool; would duplicate the profile across a connector's tools.
- **Trust the self-declared profile as-is** — rejected: a plugin (LUM-507) could declare `low` to dodge gates. The capability-derived floor is the whole point of profiling (AgentShield's rationale).
- **A per-connector DB table for profiles** — rejected for v1: profiles are static/global; per-user activation state already exists. A table adds migration cost with no per-user variance. (Plugin manifests carry their own.)
- **Emit activation risk to telemetry** (literal ticket reading) — rejected: violates the zero-telemetry invariant (ADR-046). Reframed as local-audit enrichment.
- **New third risk vocabulary** — rejected: compose the existing action-keyed `RiskTier` with the new connector-keyed `data_sensitivity`; don't replace.

---

## Dependency list (acceptance)

- **Arm A (action gate)** and **Arm B (audit enrichment)** are **shippable now** on existing seams (`check_permission`, `_write_audit_and_fire`) — no external dependency.
- **Arm C (activation gate) is BLOCKED on LUM-151** (trust ladder). The profile's `requires_trust_tier` is authored now; the full gate consumes it when LUM-151 ships. Interim coarse gate (critical→admin-confirm) bridges.
- **LUM-131 (per-action classifier)** is the natural *consumer/replacement* of Arm A's static `always_ask_actions` — the classifier reads the profile's action lists as its prior, then learns. Distinct from the "count-to-15" threshold it replaces (`permissions.routine_check`, `permissions.py:294`).
- **LUM-507 (plugin signing)** is required before a *plugin* connector's self-declared profile can be trusted below the capability-derived floor. First-party profiles need only code review.
- **LUM-141 (safety playground)** consumes `data_sensitivity`/`writes_externally` to choose which connectors get adversarial-tested.
- **LUM-333 (connector manager UI)** renders the resolved profile via `ToolCatalogEntry`.
- **LUM-217 (TELEMETRY.md)** — reconcile the ticket's "telemetry" integration to **local audit only**; no off-box emission.
- **Reconciles with** `services/api_v1_risk.py` (action-axis) and `_HARD_LIMITED` (hard floor).

Profiles to author (from the ticket, with build status): paperless `medium` (built), caldav-read `high` / caldav-write `critical` (built), ntfy (built), llm_* (built); markdown-vault `low`, email-read `high` / email-send `critical`, git `low`, photos `high` (**planned — profile authored ahead of connector**).

---

## Test-plan sketch

1. **Default-deny fallback.** `risk_profile_for("unregistered-xyz")` returns the maximum-risk profile (critical/delegate/all-always-ask). An unprofiled connector's write action is blocked in `check_permission` even in DO mode.
2. **Capability-derived floor.** A profile declaring `data_sensitivity=low` / `writes_externally=False` for a connector that has a registered `is_write=True` tool (or an `openWorldHint` MCP tool) resolves to `writes_externally=True`, `sensitivity ≥ high` — the declaration cannot undercut observable capability.
3. **`_HARD_LIMITED` precedence.** An action in `_HARD_LIMITED` is forced to require approval even if the profile lists it in `auto_execute_actions`.
4. **Action gate.** With connector mode = DO, an action in `always_ask_actions` still requires approval; a read in `auto_execute_actions` auto-executes. Assert at the single chokepoint `check_permission` and via both fan-in paths (`executor.execute`, `run_tool`).
5. **Audit enrichment.** A connector action writes an `audit_log` row carrying the profile's `external_endpoints` and `data_sensitivity`; assert the redacted stdout mirror still leaks no payload (extends `test_audit_stdout_mirror.py`).
6. **Zero-telemetry preserved.** The `TELEMETRY.md` grep gate (`posthog|mixpanel|…`) stays green after Arm B — nothing emitted off-box.
7. **Activation gate (interim).** Storing credentials for a `critical` connector requires admin confirmation and defaults mode to `ASK`.
8. **Two-namespace coverage.** `risk_profile_for` resolves a credential-registry id (`paperless`), a tool-registry id (`filesystem-mcp`), and a `capability.{id}` id — none fall through to an accidental default.

---

## Revisit conditions

- **LUM-151 trust ladder ships** → replace the interim coarse activation gate with the full `requires_trust_tier` tier check; wire the tier enum into the profile.
- **LUM-131 classifier ships** → the classifier consumes `always_ask_actions`/`auto_execute_actions` as priors; the static lists become the cold-start default.
- **LUM-507 plugin signing ships** → plugin manifests carry a signed profile; the capability-derived floor becomes the verification backstop.
- **Per-connector egress allowlist wanted** → feed `external_endpoints` into the ADR-153 allowlist so a connector may reach only its declared domains.
- **`accesses_pii` needs verification** (not just declaration) → derive from a future connector-level PII classifier or from ingest content sampling; today it is declared-only.

## Status history

- **2026-07-14:** Draft created by `/explore LUM-355` (Opus 4.8). Three surveys established: a connector-registration seam exists (two id-namespaces + `capability.*`); the Ask/Do chokepoint (`check_permission`) and audit choke (`_write_audit_and_fire`) exist and take the profile now; the trust ladder (LUM-151) does not exist, blocking the activation gate; risk-tier vocabulary exists but is UI-only; zero-telemetry forces the "telemetry" integration to local audit. Recommendation: dedicated risk registry + capability-derived floor + default-deny + three readiness-sequenced arms. Awaiting review before planning.
