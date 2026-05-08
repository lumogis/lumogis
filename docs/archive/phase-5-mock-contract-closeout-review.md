# Phase 5 mock capability contract — closeout review

**Slug:** `phase_5_mock_contract_closeout_review`  
**Date:** 2026-04-26  
**Scope:** Read-only verification of the **planning + mock test** slice for Phase 5. **No** production code changes in this pass.

**Sources:** [`phase-5-capability-contract-reference-plan.md`](phase-5-capability-contract-reference-plan.md), [`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) §3 Phase 5, [`phase-4-household-control-surface-closeout-review.md`](phase-4-household-control-surface-closeout-review.md), [`self-hosted-remediation-consolidation-review.md`](self-hosted-remediation-consolidation-review.md), [`tool-vocabulary.md`](tool-vocabulary.md), `orchestrator/tests/test_capability_contract_mock_service.py`, Core/KG modules cited below.

---

## Addendum — FU-1 closed (`query_graph_body_parity_test_or_fix`, 2026-04-26)

**Decision:** Only the **KG `query_graph` bridge** (`graph_query_tool_proxy_call`) wraps the POST body as **`{"input": {<args including user_id>}}`**, matching `services/lumogis-graph/routes/tools.py` `QueryGraphRequest`. **Generic** out-of-process capability tools continue to use a **flat** JSON body via `post_capability_tool_invocation` / `ToolExecutor` (Phase 5 mock contract unchanged).

**Code:** `orchestrator/services/capability_http.py` — `json_body={"input": payload}` in `graph_query_tool_proxy_call` only.

**Tests:** `orchestrator/tests/test_query_graph_proxy.py` — `test_query_graph_proxy_wraps_payload_for_kg_contract` + updated body assertions in `test_proxy_handler_posts_to_kg_service`.

The historical **§FU-1 analysis** below documents the bug as-found at closeout time; it is **superseded** by this addendum for current behaviour.

---

## Addendum — FU-2 closed (`audit_log_oop_fanin`, 2026-04-26)

**Decision:** Phase 3B **`try_run_oop_capability_tool`** continues to emit **`oop_tool_audit`** structlog; it now also persists **`audit_log`** rows via `services.execution.persist_tool_audit_envelope` → `actions.audit.write_audit` (`action_name` **`tool.execute.capability`**; JSON **input_summary** / **result_summary** only — no bearer tokens, no raw credential payloads).

**Code:** `orchestrator/services/execution.py` (`tool_audit_envelope_to_audit_entry`, `persist_tool_audit_envelope`); `orchestrator/services/unified_tools.py` (`_emit`).

**Tests:** `orchestrator/tests/test_oop_audit_log_fanin.py`.

---

## Addendum — FU-3 / FU-5 / FU-4 closed (`phase_5_remaining_capability_hardening`, 2026-04-26)

- **FU-3:** `build_tool_catalog_for_user` resolves `permission_mode` (`ask`, `do`, `blocked`, `unknown`) from `permissions.get_connector_mode` when a catalog row has a `connector`; capability tools use `permissions_required[0]` or `capability.{manifest.id}`. Tests: `orchestrator/tests/test_tool_catalog_permission_resolution.py`.
- **FU-5:** v1 discovery remains **`GET {base_url}/capabilities` only**; `CapabilityManifest.capabilities_endpoint` is documented as **documentary**; non-`/capabilities` values log a WARNING at registration. Tests: `orchestrator/tests/test_capability_registry.py`.
- **FU-4:** Dev-only second service **`services/lumogis-mock-capability`** + **`docker-compose.mock-capability.yml`** (not part of the default stack). Tests under `services/lumogis-mock-capability/tests/`.

---

## Executive summary

The **Phase 5 first slice** is **accurately documented** and **implemented as tests only**: [`phase-5-capability-contract-reference-plan.md`](phase-5-capability-contract-reference-plan.md) remains the contract reference; **`capability_contract_mock_service_test`** is marked **implemented** in §5 with explicit scope limits; the **remediation plan** does **not** claim full Phase 5 exit criteria met.

**Mock coverage** (`mock-echo` / `mock.echo_ping`) is **adequate** for the stated goal: generic registry → catalog → façades → HTTP proxy → executor → Phase 3B OOP, including negatives and flag-off. Façades are exercised at **service/builder** level (`build_me_tools_response`, `build_admin_diagnostics_response`), consistent with existing Phase 4 test style — not a gap for this slice.

**Production behaviour:** the mock slice adds **docs + one new test file** only; defaults unchanged (`LUMOGIS_TOOL_CATALOG_ENABLED` remains **false**).

**FU-1 (historical):** At original closeout, Core posted a **flat** body while KG expected **`{"input": …}`** — see §FU-1. **Resolved** in addendum above (`query_graph_body_parity_test_or_fix`).

---

## Phase 5 status matrix

| Item | State | Evidence |
| --- | --- | --- |
| Planning file exists + linked | ✅ | [`phase-5-capability-contract-reference-plan.md`](phase-5-capability-contract-reference-plan.md); linked from remediation plan Phase 5 header |
| Mock test slice complete | ✅ | Plan §5 **Status: Implemented** → `orchestrator/tests/test_capability_contract_mock_service.py` |
| Full Phase 5 exit list “all green” | ⏸️ Not claimed | Remediation §3 Phase 5 **programme** still lists product exit bullets (e.g. richer “why unavailable”); **capability scaffolding** slice is closed |
| FU-1 `query_graph` body parity | ✅ **Closed** (2026-04-26) | Core wraps `{"input": payload}`; see addendum |
| `audit_log` OOP fan-in | ✅ **Closed** (2026-04-26) | `persist_tool_audit_envelope` + `write_audit`; see FU-2 addendum |
| Catalog `permission_mode` / Ask-Do façade | ✅ **Closed** (2026-04-26) | FU-3 — `get_connector_mode` in read model |
| `capabilities_endpoint` vs discovery | ✅ **Closed** (2026-04-26) | FU-5 — documentary field + warning |
| Second capability compose smoke | ✅ **Closed** (2026-04-26) | FU-4 — `lumogis-mock-capability` overlay |
| Richer unavailable reasons | ⏸️ Partial | Catalog/façade still coarse vs remediation exit §4 bullet |
| Invoke contract convention-only | ⏸️ Yes | `{base}/tools/{name}` not manifest-declared |
| mTLS / signed manifests / marketplace / cloud | ⏸️ Deferred | Plan §7 / Phase 6 unchanged |

---

## Mock capability test coverage review

**File:** `orchestrator/tests/test_capability_contract_mock_service.py`

| Requirement | Test function(s) | Assessment |
| --- | --- | --- |
| Discovery `GET /capabilities` + health | `test_registry_discovers_manifest_and_health_via_mock_transport` | ✅ |
| Catalog row healthy/unhealthy | `test_build_tool_catalog_mock_echo_healthy_and_unhealthy` | ✅ |
| `/api/v1/me/tools` safe path (service-level) | `test_me_tools_response_safe_metadata_for_mock_capability` | ✅ **Adequate** (builder parity with Phase 4 style) |
| `/api/v1/admin/diagnostics` summary (service-level) | `test_admin_diagnostics_includes_mock_echo_counts_and_no_urls` | ✅ **Adequate** |
| Generic HTTP invocation | `test_post_capability_tool_invocation_via_mock_transport` | ✅ |
| `ToolExecutor` allow + deny + audit | `test_execute_capability_http_audit_on_deny_and_allow` | ✅ |
| Phase 3B OOP flag-on | `test_oop_dispatch_success_headers_and_body` | ✅ |
| Flag-off no OOP | `test_flag_off_no_llm_tool_no_dispatch_no_http` | ✅ |
| Missing bearer fail-closed | `test_missing_bearer_no_oop_entry_no_http` | ✅ |
| Permission denied | `test_permission_denied_no_http` | ✅ |
| `X-Lumogis-User` not auth | `test_attribution_without_bearer_rejected_by_mock_endpoint` | ✅ |
| No graph id/name | `MOCK_CAP_ID` / `MOCK_TOOL` | ✅ |

**Verdict:** **Adequate** for the mock contract slice. **Thin** only in the sense that HTTP **route** tests for `/api/v1/me/tools` and `/api/v1/admin/diagnostics` are not duplicated here — existing `test_api_v1_*.py` already cover routes; this file correctly reuses **builders** for injected registry semantics.

**Missing (optional, not required for this closeout):** explicit `TestClient` round-trip for mock registry (would duplicate Phase 4 patterns).

---

## Production-behaviour verification

| Check | Result |
| --- | --- |
| Production/runtime code changed by mock slice | **No** (this slice: tests + architecture docs only) |
| New Docker image / second service | **No** |
| DB migrations | **No** |
| `loop.py` changed for mock | **No** (`loop.py` still imports `prepare_llm_tools_for_request` / `finish_llm_tools_request` only) |
| `mcp_server.py` changed | **No** |
| New graph-specific code in `services/tools.py` | **No** |
| Default `LUMOGIS_TOOL_CATALOG_ENABLED` | **`false`** (`config.get_tool_catalog_enabled` — env unset → false) |

*Note:* This closeout did not re-run a full `git diff` against an earlier baseline; conclusions align with the **declared scope** of `capability_contract_mock_service_test` and file inspection.

---

## FU-1 — `query_graph` HTTP body parity analysis *(historical — pre-fix)*

*As of the original closeout, before `query_graph_body_parity_test_or_fix`:*

### What Core sent (bug)

`graph_query_tool_proxy_call` called `post_capability_tool_invocation(..., json_body=payload, ...)` with a **flat** object — **not** wrapped under `input`.

### What KG expects

`QueryGraphRequest` is a Pydantic model with a single field **`input: dict`**. The handler calls `query_graph_tool(body.input)`.

```53:82:services/lumogis-graph/routes/tools.py
class QueryGraphRequest(BaseModel):
    ...
    input: dict = Field(default_factory=dict)
...
    output = query_graph_tool(body.input)
```

### Compatibility

For a JSON body `{"mode":"ego","entity":"Ada","user_id":"u"}`, Pydantic v2 validation yields **`input: {}`** (extra top-level keys are ignored by default). The tool then sees an **empty** spec → **not** the intended LLM arguments.

A wrapped body `{"input":{"mode":"ego",...}}` validates correctly.

### What tests proved *(pre-fix vs post-fix)*

| Side | Tests | Pre-fix | Post-fix |
| --- | --- | --- | --- |
| Core | `orchestrator/tests/test_query_graph_proxy.py` | Asserted **flat** outbound body (bug) | Asserts **`{"input": …}`** (`test_query_graph_proxy_wraps_payload_for_kg_contract`) |
| KG | `services/lumogis-graph/tests/test_tools.py` | **`{"input": …}`** | Unchanged |

**Conclusion (historical):** Real **`GRAPH_MODE=service`** risk — empty `input` on KG.

**Fix applied:** Core-only wrap `json_body={"input": payload}` in `graph_query_tool_proxy_call`; tests updated. No dual-shape support on KG; generic OOP tools unchanged (flat).

---

## Updated Phase 5 risk register (post–mock slice)

| Risk | Severity | Blocks “full” Phase 5 (remediation exit §5)? | Follow-up slug |
| --- | --- | --- | --- |
| **FU-1** `query_graph` flat vs `input` wrapper | — (was **High**) | **Closed** | `query_graph_body_parity_test_or_fix` ✅ |
| **`audit_log` OOP fan-in** | — (was **Medium**) | **Closed** | `audit_log_oop_fanin` ✅ |
| **Invoke URL convention-only** (`/tools/{name}`) | Medium | No | `capability_invoke_contract_v1` |
| **Catalog `permission_mode` / Ask-Do** | — (was **Medium**) | **Closed** | `tool_catalog_permission_resolution` ✅ |
| **Unavailable reasons coarse** | Low–medium | Partial vs exit #4 | extend `me_tools` / catalog (same slug family) |
| **Real capability packaging / compose** | — (was **Medium**) | **Closed** (dev overlay) | `phase_5_remaining_capability_hardening` / `lumogis-mock-capability` ✅ |
| **KG auth legacy** (`require_service_bearer=False` on graph proxy) | Medium (posture) | No | `kg_capability_auth_hardening` |
| **`capabilities_endpoint` vs discovery** | — (was **Low**) | **Closed** | FU-5 — documentary + WARNING ✅ |

---

## Recommended next step — *updated after FU-1…FU-5 closeout*

**`capability_invoke_contract_v1`** (manifest-declared invoke base path) **or** product-specific **real** capability work — Phase 5 **self-hosted hardening** slice is closed.

**Rationale:** FU-1…FU-5 are **closed** (see plan §6b and addenda). Remaining catalogue/invoke gaps are **contract formalisation** or **shipping a non-mock capability**, not Core scaffolding.

**Alternative:** **D — stop Phase 5** here and return to product work until another Phase 5 exit criterion bites.

---

## Follow-up backlog (copy-forward)

| Priority | Slug / item |
| --- | --- |
| ~~P0~~ | ~~`query_graph_body_parity_test_or_fix` (FU-1)~~ — **done** |
| ~~P1~~ | ~~`audit_log_oop_fanin`~~ — **done** (2026-04-26) |
| ~~P1~~ | ~~`tool_catalog_permission_resolution`~~ — **done** (2026-04-26) |
| P2 | `capability_invoke_contract_v1` |
| ~~P2~~ | ~~Real second service / compose smoke~~ — **done** (`lumogis-mock-capability` overlay, 2026-04-26) |
| ~~P2~~ | ~~FU-5 discovery vs `capabilities_endpoint`~~ — **done** (2026-04-26) |

---

## Verification commands

| Command | Result (this environment) |
| --- | --- |
| `rg 'mock-echo|mock.echo_ping' orchestrator/tests/test_capability_contract_mock_service.py` | **`rg` not installed** — used workspace grep: matches present |
| `rg 'graph_query_tool_proxy_call|…'` across services/tests | Not run (`rg` unavailable); paths confirmed via read/grep tool |
| `pytest …test_capability_contract_mock_service.py …test_query_graph_proxy.py` | **20 passed** (venv `/tmp/lumogis-pytest-venv`) |

---

## Related documents

- [`phase-5-capability-contract-reference-plan.md`](phase-5-capability-contract-reference-plan.md) — contract + mock slice status  
- [`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) — Phase 5 programme context  
- [`tool-vocabulary.md`](tool-vocabulary.md) — catalog vs execution terms  
