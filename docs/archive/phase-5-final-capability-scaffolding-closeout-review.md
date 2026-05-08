# Phase 5 — Final capability scaffolding closeout review

**Slug:** `phase_5_final_capability_scaffolding_closeout_review`  
**Date:** 2026-04-26  
**Kind:** Read-only programme verification + documentation (no new production functionality).

**Sources:** [`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) §3 Phase 5, [`phase-5-capability-contract-reference-plan.md`](phase-5-capability-contract-reference-plan.md), [`phase-5-mock-contract-closeout-review.md`](phase-5-mock-contract-closeout-review.md), [`phase-4-household-control-surface-closeout-review.md`](phase-4-household-control-surface-closeout-review.md), [`tool-vocabulary.md`](tool-vocabulary.md), Core modules under `orchestrator/services/`, `services/lumogis-mock-capability/`, and tests cited below.

---

## 1. Executive summary

Phase 5 **self-hosted capability scaffolding** — optional services, household LAN trust, generic HTTP tool path, catalog/diagnostics façades, audit fan-in, and dev-only packaging smoke — is **sufficiently complete** to **pause** this remediation slice and return to **product work**. No Phase 6 marketplace, cloud multi-tenant, mTLS-by-default, signed manifests, public Plugin SDK, or third-party sandbox work was started.

**Authoritative status** for FU-1…FU-5 and chunk slugs lives in [`phase-5-capability-contract-reference-plan.md`](phase-5-capability-contract-reference-plan.md) §6b and the remediation plan §3 Phase 5 header. Older documents (e.g. [`phase-4-household-control-surface-closeout-review.md`](phase-4-household-control-surface-closeout-review.md), [`self-hosted-remediation-consolidation-review.md`](self-hosted-remediation-consolidation-review.md)) may still list legacy follow-up rows; when they conflict, prefer **this file** and the **remediation plan**.

**Verify-plan addendum (2026-04-26):** Full Docker backend verification passed after two hygiene fixes discovered during `/verify-plan`: the OOP route builder now captures the per-service bearer id correctly for multiple capability services, and the modified `CapabilityManifest` model was re-vendored to `services/lumogis-graph/models/capability.py`. A pre-existing Web Push count query also received the required `# SCOPE-EXEMPT:` scanner annotation. Final results: `make compose-test` **1533 passed / 9 skipped / 0 failed**; mock capability service tests **5 passed**.

---

## 2. Completed Phase 5 matrix

| Chunk / slug | Outcome | Evidence (representative) |
| --- | --- | --- |
| `phase_5_capability_contract_reference_plan` | Reference contract + matrix documented | [`phase-5-capability-contract-reference-plan.md`](phase-5-capability-contract-reference-plan.md) |
| `capability_contract_mock_service_test` | Non-graph mock capability through generic path | `orchestrator/tests/test_capability_contract_mock_service.py` |
| `query_graph_body_parity_test_or_fix` (FU-1) | KG bridge `{"input": payload}`; generic OOP flat | `orchestrator/tests/test_query_graph_proxy.py`, `graph_query_tool_proxy_call` |
| `audit_log_oop_fanin` (FU-2) | OOP → `audit_log` via `write_audit` | `persist_tool_audit_envelope`, `test_oop_audit_log_fanin.py` |
| `tool_catalog_permission_resolution` (FU-3) | `/me/tools` + catalog `permission_mode` from `get_connector_mode` | `build_tool_catalog_for_user`, `test_tool_catalog_permission_resolution.py` |
| `capabilities_endpoint` vs discovery (FU-5) | v1 discovery = `GET {base}/capabilities` only; field documentary + WARNING | `CapabilityManifest`, `capability_registry.py`, `test_capability_registry.py` |
| `phase_5_remaining_capability_hardening` / FU-4 | Dev-only second service + overlay compose | `services/lumogis-mock-capability/`, `docker-compose.mock-capability.yml`, `make mock-capability-test` |
| `/verify-plan` hardening fixes | Multi-capability bearer closure regression covered; vendored capability model synced; scoped SQL scanner annotation added | `test_oop_dispatch_uses_bearer_for_each_capability_service`; `test_vendored_models_in_sync.py`; `test_no_raw_user_id_filter_outside_admin.py` |

**Phase 6** remains **deferred** per remediation plan §3 Phase 6 table — no implementation work started.

---

## 3. Phase 5 exit criteria assessment (remediation plan §3 Phase 5)

Criteria reproduced from [`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) (numbered list under **Exit criteria**).

| Criterion | Assessment | Notes |
| --- | --- | --- |
| **1.** `lumogis-graph` documented as reference capability shape + contract list | **Satisfied** | Phase 5 plan §10; ADR 011; `services/lumogis-graph/`; remediation Phase 5 purpose. |
| **2.** Second **mock** capability end-to-end through generic executor path without graph shims in `tools.py` / `loop.py` | **Satisfied** | `test_capability_contract_mock_service.py`; OOP via `try_run_oop_capability_tool` + `ToolExecutor`. |
| **3.** New capability services do not require Core Postgres/Qdrant credentials | **Satisfied** (policy + fixtures) | Stated in remediation Phase 5 **In scope** §3; mock + `lumogis-mock-capability` carry no Core DB secrets. **Integration enforcement** (compose policy gates) remains operator/process-level, not a Phase 5 code deliverable. |
| **4.** Core can report **why** a capability tool is unavailable; `/api/v1/me/tools` surfaces reason | **Partially satisfied** | `available`, `why_not_available`, health/unhealthy paths, `permission_mode`, bearer/permission tests cover major cases. **Richer**, user-tuned copy for every edge case (timeout vs transport vs HTTP body) is **future hardening** (`me_tools` / catalog messaging). |
| **5.** Household LAN security model (shared secret, attribution not auth, mTLS not required) | **Satisfied** | Phase 5 plan + remediation; KG optional bearer on graph proxy remains **posture / future hardening** (`kg_capability_auth_hardening`), not a Phase 5 blocker. |

---

## 4. Production behaviour boundary verification

| Check | Result |
| --- | --- |
| No second capability in **default** `docker-compose.yml` | **Verified** — no `lumogis-mock-capability` / `mock-capability` in default compose. |
| Mock capability **opt-in** | **`docker-compose.mock-capability.yml`** + README; not in default stack. |
| Phase 5 **DB migrations** | **None required** for this programme slice (no new audit tables; existing `audit_log`). |
| **MCP** behaviour | **Unchanged** by Phase 5 scaffolding work (guarded elsewhere, e.g. `test_mcp_manifest_unchanged.py`). |
| Default **`LUMOGIS_TOOL_CATALOG_ENABLED`** | **`false`** in `config.get_tool_catalog_enabled()`. |
| Generic capability POST body | **Flat** JSON via `post_capability_tool_invocation` (non-KG). |
| KG **`query_graph`** bridge body | **`{"input": payload}`** in `graph_query_tool_proxy_call`. |
| **`run_tool`** | Still uses in-process **`ToolSpec`** first; OOP bridge flag-gated. |
| OOP **audit** fan-in | **Safe summaries** only (`tool_audit_envelope_to_audit_entry`); no bearer/raw bodies in audit rows. |
| **`/api/v1/me/tools` `permission_mode`** | **Read-model only**; **`check_permission`** unchanged on execution. |
| **`capabilities_endpoint` mismatch** | **WARNING** log only; discovery URL **unchanged** (`/capabilities`). |
| **Private audit docs / ADRs** | **Not edited** in this review task. |

---

## 5. Test coverage inventory

| Area | Tests | Adequacy |
| --- | --- | --- |
| Mock capability contract path | `test_capability_contract_mock_service.py` | **Adequate** — discovery, catalog, me tools, admin diagnostics, HTTP invoke, executor deny/allow, OOP flag-on/off, missing bearer, permission denied, attribution-not-auth, and distinct bearer resolution across multiple services. |
| `query_graph` body parity | `test_query_graph_proxy.py` | **Adequate** — wrapped KG contract. |
| OOP audit fan-in | `test_oop_audit_log_fanin.py` | **Adequate** — mapping, success/deny/HTTP error, injected `write_audit`. |
| Catalog permission resolution | `test_tool_catalog_permission_resolution.py` | **Adequate** — do/ask/blocked/unknown, capability connector, lookup failure soft. |
| Registry discovery + `capabilities_endpoint` warning | `test_capability_registry.py` | **Adequate** — documentary field + log. |
| Dev mock HTTP service | `services/lumogis-mock-capability/tests/test_app.py` | **Adequate** — health, manifest, bearer gate. |
| Generic OOP dispatch / fail-closed | `test_llm_capability_tool_dispatch.py`, `test_loop_oop_tool_isolation.py` | **Adequate** (existing Phase 3B suite). |
| Default stack excludes mock service | **Documented** + compose grep; **not** a dedicated CI assertion (acceptable). |

**Thin / missing (non-blocking):** automated CI assertion that `docker-compose.yml` never gains the mock service without review; optional future **integration** smoke invoking overlay compose.

---

## 6. Final Phase 5 follow-up register

### A. Self-hosted hardening / productisation (Phase 5-adjacent, not Phase 6)

| Follow-up | Severity | Blocks pausing Phase 5 scaffolding? | Suggested slug |
| --- | --- | --- | --- |
| Formal **invoke URL** in manifest vs `{base}/tools/{name}` convention | Medium | **No** | `capability_invoke_contract_v1` |
| **Richer “why unavailable”** copy on `/me/tools` + catalog | Low–medium | **No** | extend `me_tools` / catalog messaging (same family as remediation exit #4 partial) |
| **KG** optional bearer / graph proxy auth posture | Medium (posture) | **No** | `kg_capability_auth_hardening` |
| **Real** premium / product capability (non-mock) | Product | **No** | product-specific |
| Compose/policy **enforcement** that new capabilities never receive Core DB creds | Low–medium | **No** | ops/docs or future guard |

### B. Phase 6 / future ecosystem (explicitly deferred)

| Follow-up | Severity | Blocks pausing Phase 5? | Notes |
| --- | --- | --- | --- |
| mTLS by default | P3 / defer | **No** | Remediation Phase 6 |
| Signed manifests + marketplace | P3 / defer | **No** | Phase 6 |
| Public Plugin SDK | P3 / defer | **No** | Phase 6 |
| Third-party connector sandbox | P3 / defer | **No** | Phase 6 |
| Cloud multi-tenant | P3 / defer | **No** | Phase 6 |
| Full sync→async migration | P3 / defer | **No** | Phase 6 |

---

## 7. Recommended final decision

**A. Phase 5 is sufficiently complete for self-hosted capability scaffolding; pause remediation and return to product work.**

**Rationale:** Discovery, generic invoke path, catalog + permission **labelling**, durable OOP audit, KG body parity, discovery contract clarity, mock **and** dev-only compose proof are in place. Remaining gaps are **hardening and productisation** (richer unavailable copy, manifest invoke formalisation, real capability SKU), not missing **scaffolding**. Phase 6 scope is **unchanged and not started**.

**Not recommended:** **C** (Phase 6) — no trigger from this review. **B** — no single blocking chunk identified. **D** — no consolidation ADR required for this closeout; architecture docs + tests suffice.

---

## 8. Why Phase 6 remains deferred

Phase 6 in the remediation plan bundles **marketplace**, **signed manifests**, **public SDK**, **sandbox**, **cloud multi-tenant**, **mTLS-by-default**, and **async migration** — explicitly **household-disproportionate** work. Phase 5 delivered **LAN-appropriate** optional capabilities without those primitives; starting Phase 6 would **change programme scope and operator burden**, not close a Phase 5 gap.

---

## 9. Verification commands (as run or attempted)

| Command | Result |
| --- | --- |
| `rg 'lumogis-mock-capability|docker-compose.mock-capability|mock-capability-test' .` | **`rg` not available** in this environment; used workspace **grep** — matches in Makefile, overlay compose, README, docs. |
| `rg 'query_graph_body_parity…|audit_log_oop_fanin|tool_catalog_permission_resolution' docs/architecture` | Used **grep** — hits in phase-5 docs, remediation, consolidation, phase-4 (legacy rows). |
| `rg 'LUMOGIS_TOOL_CATALOG_ENABLED' orchestrator/config.py …` | **grep** — default **false** in `config.py`; references in `unified_tools.py`; `loop.py` uses `prepare_llm_tools_for_request` (flag read inside unified_tools). |
| `rg 'tool.execute.capability|persist_tool_audit…' orchestrator/…` | **grep** — present under `execution.py`, `unified_tools.py`, tests. |
| `rg 'capabilities_endpoint' orchestrator/models/…` | **grep** — model + registry + tests. |
| `make test` | **Unavailable on host** — `/usr/bin/python3: No module named pytest`; verified with Docker-backed suite instead. |
| Focused Docker pytest (`test_capability_contract_mock_service.py`, `test_query_graph_proxy.py`, `test_oop_audit_log_fanin.py`, `test_tool_catalog_permission_resolution.py`, `test_capability_registry.py`, `test_api_v1_me_tools.py`, `test_tool_catalog_capability_discovery.py`, `test_tool_catalog_includes_core_tools.py`, `test_tool_executor_audit.py`) | **66 passed**. |
| Mock capability service pytest (`PYTHONPATH=services/lumogis-mock-capability … pytest services/lumogis-mock-capability/tests -q`) | **5 passed**. |
| Scope/vendoring regression checks after fixes | `test_no_raw_user_id_filter_outside_admin.py`, `test_api_v1_me_notifications.py`, `test_vendored_models_in_sync.py`, `test_capability_contract_mock_service.py` all passed in targeted reruns. |
| `make compose-test` | **1533 passed / 9 skipped / 0 failed** (Docker-backed full backend suite). |

---

## 10. Related documents

- [`phase-5-capability-contract-reference-plan.md`](phase-5-capability-contract-reference-plan.md) — contract reference + FU register  
- [`phase-5-mock-contract-closeout-review.md`](phase-5-mock-contract-closeout-review.md) — incremental closeout addenda  
- [`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) — §3 Phase 5–6 programme context  
- [`tool-vocabulary.md`](tool-vocabulary.md) — catalog vs execution terms  

**Review complete.**
