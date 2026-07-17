# ADR-169 — Capability invoke contract v1: manifest-declared invoke, versioned wire envelope (LUM-41)

**Status:** Accepted (implemented)
**Created:** 2026-07-14
**Decided by:** `/explore` (Opus 4.8)
**Linear:** [LUM-41](https://linear.app/lumogis/issue/LUM-41) (parent LUM-78; project Capabilities / Plugins; milestone v1.3)
**Builds on:** ADR-010 (ecosystem plumbing), ADR-011 (capability system), the phase-5 capability-contract reference plan (`docs/private/architecture/phase-5-capability-contract-reference-plan.md`), LUM-42 (KG auth hardening — done)
**Feeds:** LUM-241 (Extension Contract v1 / author guide), LUM-171 (marketplace), LUM-507 (plugin permission/sandbox), LUM-355 (connector/capability risk)

---

## Context

LUM-41 is the **keystone of the plugin/marketplace programme**: the capability *invoke contract* is the ABI external developers build to, and LUM-171 (marketplace) + LUM-241 (author guide) + LUM-507 (permissions) all require it to be **stable and self-describing** before the ecosystem can open. Today it is neither — it is a set of **hardcoded Core conventions** that the manifest only *documents*, and the one real capability (the KG service) doesn't even conform to them.

### What actually exists today (survey)

- **Discovery works and is generic:** `CAPABILITY_SERVICE_URLS` → Core fetches `GET {base_url}/capabilities`, validates the body as `CapabilityManifest` (`models/capability.py`), health-probes `base_url + health_endpoint`, gates on `min_core_version`, caches by `manifest.id`, re-discovers every 5 min. Failures are soft (service skipped, never raises).
- **The invoke path is a HARDCODED convention, not manifest-declared.** `services/capability_http.py::post_capability_tool_invocation` builds `url = f"{base}/tools/{tool_name}"` — the module docstring says outright: *"CapabilityManifest does not yet pin a full invoke URL; Core follows the lumogis-graph shape `{base}/tools/{tool_name}`."* Body shape is caller-decided (generic path = flat `{...args, user_id}`; KG bridge = wrapped `{"input": ...}`). Headers: `X-Lumogis-User` (attribution only, **not** auth) + optional `Authorization: Bearer`.
- **Several manifest fields are "documentary" — declared but ignored.** `capabilities_endpoint` is on the model but Core hardcodes `/capabilities` (logs a warning if they differ). `output_schema`/`input_schema` exist but Core "does not validate schema correctness until a tool is invoked" — and in practice never validates output at all.
- **The reference implementation does not conform to its own manifest.** The KG `/capabilities` manifest declares **six** tools (`graph.query_ego`, `graph.query_path`, `graph.query_mentions`, `graph.get_context`, `graph.backfill`, `graph.health`) but the service exposes exactly **one** invoke endpoint, `POST /tools/query_graph`, with a wrapped `{"input": …}` body and a `{"output": …}` response. Applying the generic `{base}/tools/{name}` rule to the discovered manifest would POST to `/tools/graph.query_ego` → **404**. The declared tool names and the real endpoints are **disjoint**; the only working KG invoke is a hardcoded in-process bridge (`kg_premium_core.graph_query_tool_proxy_call`). This is the sharpest proof the contract is unformalized.
- **Auth is a Core-side convention, fail-closed.** Per-service bearer from env `LUMOGIS_CAPABILITY_BEARER_<ID>`; unset → the tool is omitted from the LLM list (fail-closed). LUM-42 hardened this: fail-open survives only behind explicit opt-ins (`LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET`, `KG_ALLOW_INSECURE_WEBHOOKS`).
- **No contract version, no structured error surfaced.** Only `manifest.version` (the service's own) + `min_core_version`. Every invoke failure collapses to `HttpInvokeResult(ok=False, text="unavailable", error_reason=…)` — Core **discards** the structured error the KG service already returns (`{detail, reason, elapsed_ms}` on 422/504/500). All timeouts are Core-side constants; no streaming.

---

## Decision (recommended)

**Make the manifest the self-describing source of truth for invocation, standardise the wire envelope, and version the contract** — so a capability author declares *how Core reaches each tool* and Core stops assuming conventions. Ten formalisations, all additive with back-compat defaults that reproduce today's behaviour:

1. **Per-tool declared invoke — decouple `name` from route.** Add to `CapabilityTool`: `invoke: { method: "POST" (default), path: str }` where `path` defaults to `/tools/{name}`. The LLM-facing `name` (e.g. `graph.query_ego`) becomes independent of the HTTP route (`/tools/query_graph`). **Fixes the KG disjoint-names bug** and lets a service map many tools onto one endpoint (or vice-versa).
2. **Standard request envelope (versioned, wrapped).** Core POSTs `{ "contract_version": "1.0", "tool": "<name>", "arguments": {…}, "meta": { "user": "<attribution>", "request_id": "<uuid>" } }`. Wrapped (not flat) so envelope metadata never collides with a tool's own argument names. Retires the flat-vs-`{"input":…}` inconsistency; `X-Lumogis-User` stays as a redundant header for logging.
3. **Standard response envelope.** `{ "ok": true, "output": <result> }` on success; `{ "ok": false, "error": { "code": "...", "message": "...", "retryable": bool } }` on failure. Core parses it (instead of returning `resp.text`), validates `output` against the tool's `output_schema` when present, and surfaces `error.message` to the LLM.
4. **Structured error contract.** Fixed `code` vocabulary (`invalid_arguments`, `unauthorized`, `not_found`, `timeout`, `unavailable`, `internal`) + `retryable`. Core maps transport failures into the same shape and stops flattening the KG service's already-structured errors.
5. **Per-tool `is_write` + `idempotent`.** Add both to `CapabilityTool` (today `is_write` is hardcoded `False` on the route). Feeds permissioning (LUM-507), risk profiling (LUM-355/ADR-163), and retry-safety.
6. **Manifest-declared auth.** `auth: { mode: "bearer" | "none", credential_ref: "<key>" }` on the manifest (default `bearer`). Replaces the implicit `LUMOGIS_CAPABILITY_BEARER_<ID>` convention with a declared binding; keeps fail-closed default; the KG legacy fail-open opt-ins become deprecated (LUM-42 already fail-closed by default).
7. **Per-tool `timeout_ms` (optional, Core-clamped).** The manifest may request a budget; Core clamps to a hard ceiling. Removes the four scattered Core-side timeout constants as the *only* control.
8. **Explicit contract version.** Add `contract_version: "1.0"` to `CapabilityManifest` (distinct from service `version` / `min_core_version`). Core negotiates: unknown **major** → refuse-and-warn; unknown **minor** → accept (forward-compatible).
9. **Honour `capabilities_endpoint` + `health_endpoint`.** Stop hardcoding `/capabilities`; use the declared paths (defaults preserve today's `/capabilities`, `/health`). Removes the "documentary field" anti-pattern so the manifest is *load-bearing*.
10. **Streaming: explicitly OUT for v1.** Single request/response; the contract states it, so a v2 can add it without ambiguity.

**Conformance is proven by two implementations, not one.** The KG service is fixed to conform (declare the real `/tools/query_graph` invoke path + one canonical tool, or grow the six endpoints — author's choice, but manifest and reality must match), and a **minimal reference "echo" capability** (already used in tests) is promoted to the second conformance target + the example in LUM-241's author guide. A `contract_conformance` test suite runs both against the v1 envelope.

---

## Alternatives considered

- **Keep the hardcoded `{base}/tools/{name}` convention, document it as the contract.** Rejected: it's already broken for the KG reference impl (disjoint names), can't express one-endpoint-many-tools, and pins the LLM tool name to the URL — a poor ABI to ask external devs to build to.
- **Flat request body (status quo generic path).** Rejected: envelope metadata (user, request_id, contract_version) collides with tool argument names; wrapping is the only forward-compatible shape.
- **MCP as the capability transport instead of HTTP tools.** Interesting but out of scope — the manifest already has a `transport: http|mcp` enum; v1 formalises the **http** contract (the shipped one). An MCP-transport capability contract is a separate follow-up, and note LUM-610 (Lumogis-as-MCP-client) evaluates the adjacent question.
- **Full JSON-Schema validation of every invoke (in + out).** Deferred: make output-schema validation opt-in in v1 (perf + author friction); input validation stays at the capability. Revisit once authors rely on it.
- **Signed manifests / mTLS-by-default now.** Explicitly Phase 6 (per the closeout doc) — that's LUM-507 (permission/sandbox/signing) + LUM-510 (signing keys), not this contract.

---

## Dependencies & sequencing

- **This unblocks the marketplace chain.** LUM-171 lists "LUM-41 stable" as prerequisite #1; LUM-241 (author guide) *documents this contract*; LUM-507 consumes the declared `is_write`/`auth`/`permissions_required` for grants; LUM-355/ADR-163 can derive a capability risk profile from the declared `is_write` + `auth` + endpoints.
- **Blocked-by (now resolved):** LUM-238 (readiness audit, Done), LUM-42 (auth posture, Done).
- **Reference-impl work:** fixing the KG manifest↔endpoint mismatch is part of this (it's the conformance proof), and interacts with `kg_premium_core` + the `/tools/query_graph` bridge.
- **Not this ticket:** signed manifests, sandbox, mTLS-by-default, the public Plugin SDK, streaming (all Phase 6 / LUM-507).

## Test-plan sketch

1. **Conformance suite (`test_capability_contract_v1.py`):** a reference echo capability + the KG service both validate against the v1 manifest schema; a declared-path tool is invoked at its `invoke.path` (not `/tools/{name}`), proving decoupling.
2. **Envelope round-trip:** Core sends the wrapped request envelope; capability returns the success envelope; Core validates `output` against `output_schema`; assert the LLM receives `output`, not raw text.
3. **Structured error surfaced:** capability returns `{ok:false, error:{code:"timeout", retryable:true}}` → Core surfaces `error.message` and marks retryable (vs today's flattened "unavailable").
4. **Version negotiation:** unknown **major** `contract_version` → service refused at registration with a WARNING; unknown **minor** → accepted.
5. **Auth from manifest:** `auth.mode="bearer"` with unset credential → tool fail-closed omitted; `auth.mode="none"` → invoked without bearer; deprecated legacy opt-in still honoured behind its env flag with a deprecation warning.
6. **KG regression:** the fixed KG manifest's declared invoke path resolves to a real endpoint (no `/tools/graph.query_ego` 404); `query_graph` still works via the declared path.
7. **Back-compat:** a manifest with none of the new fields (only today's fields) still registers and invokes via the defaulted `/tools/{name}` + legacy body — no existing capability breaks.

## Revisit conditions

- **Streaming** requested by an author → contract v1.1 adds a streamed response mode.
- **MCP-transport capabilities** become real → a sibling MCP invoke contract (coordinate with LUM-610).
- **Signed manifests land (LUM-507/510)** → add a `signature` block + verification to the manifest; contract minor-bump.
- **Output-schema validation** proves valuable → make it default-on.

## Planning refinements (2026-07-14, `/create-plan LUM-41`)

Two decisions taken at planning that refine the draft above:

- **Back-compat → hard cut, no dual-envelope path.** Because there are **no external capabilities yet** (only the two first-party ones, KG + the mock), Core does **not** build a legacy `contract_version`-fallback path. The two existing capabilities are **migrated** to the v1 contract in this chunk; Core speaks v1 only. (The `contract_version` field still ships for *future* forward-compat negotiation — it just has no legacy branch to fall back to.) Supersedes the "version-gated dual support" framing.
- **Output-schema validation → mandatory when declared** (not opt-in). An unenforced `output_schema` would be exactly the "documentary field" anti-pattern this ADR removes. So: when a tool declares a non-trivial `output_schema`, Core **validates** the invoke response against it and returns a structured `invalid_output` error on mismatch; a loosely-typed tool declares `{"type":"string"}` or omits the schema (= "any"). Supersedes item #5's "opt-in" and the "Output-schema validation proves valuable → default-on" revisit note.

## Status history

- **2026-07-14:** Draft created by `/explore LUM-41` (Opus 4.8), grounded in the phase-5 capability-contract reference plan + a live survey. Found the invoke path is a hardcoded `{base}/tools/{name}` convention and the KG reference impl's declared tool names are disjoint from its single real endpoint. Recommends 10 additive formalisations (declared invoke path, versioned wrapped request/response envelopes, structured errors, manifest-declared auth, per-tool is_write/idempotent/timeout, contract version, honoured discovery/health paths, streaming-out) proven by a two-implementation conformance suite. Awaiting review before planning.
- **2026-07-14:** `/create-plan LUM-41` — scope confirmed (all 10 formalisations), back-compat hard-cut (no external capabilities), output validation mandatory-when-declared, KG manifest fixed in-scope + follow-up KG tickets for live-invoke. See planning refinements above; plan at `.cursor/plans/LUM-41-capability-invoke-contract-v1.plan.md`.
- **2026-07-15:** **Implemented** on `dev` @ `835d9d945` (merge `claude/lum-41-capability-invoke-contract`). Core speaks invoke contract v1 only (hard cut); KG reference impl migrated to the versioned envelope; five aspirational `graph.*` tools removed from the shipped manifest (manifest must not advertise 404s) — live invoke for those endpoints is a **child issue under LUM-78** (see plan Follow-up register). `/verify-plan` 2026-07-15: orchestrator conformance **66** targeted tests green; KG service tests **11/11** green.
