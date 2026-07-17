# ADR-162 — Default-deny KG exposure per surface/client (LUM-559)

**Status:** Draft (exploration) — recommendation for review, not yet implemented
**Created:** 2026-07-14
**Decided by:** `/explore` (Opus 4.8), three parallel codebase surveys
**Linear:** [LUM-559](https://linear.app/lumogis/issue/LUM-559) (project: Auth / Users / Credentials; milestone v1.2; Urgent)
**Builds on:** ADR-015 (personal/shared/system scopes), ADR-130/LUM-531 (MCP token scopes), ADR-112/LUM-334 (household RBAC), ADR-106 (unscoped auto-RAG)
**Blocks:** LUM-166 (voice) · **Informs:** LUM-172 (Home Assistant) · **Complements:** LUM-157 (publish/unpublish), LUM-131 (per-action Allow/Ask/Block)

---

## Context

Lumogis models **who** may recall an entity — ADR-015's `scope` (personal/shared/system) enforced through the `visible_filter` helper family, membership-gated by `allows_shared` (LUM-577). It does **not** model **where** — on which client/surface — an authorised user may recall it. A household member is authorised for a `shared` financial entity, but that does not mean it should be spoken aloud by a living-room voice endpoint, surfaced on a child's thin client, or returned to an external MCP tool.

Compass Layer-2 (2026-07-02, idea 11) names the pattern from Home Assistant: **nothing is exposed to a surface until opted in.** LUM-559 asks for a **default-deny exposure matrix**:

```
{ entity_type × sensitivity_class × surface × role } → exposed | hidden (default)
```

This ADR is the exploration deliverable: a recommended schema, an enforcement chokepoint, a dependency list, and a test-plan sketch.

### What the code survey found (the constraints that shape everything)

Three surveys of `lumogis-app` establish the ground truth. **Two of the four matrix axes do not exist today**, so LUM-559 is not one feature — it is a prerequisite chain.

**Axis `surface` — DOES NOT EXIST.** The authenticated principal is `UserContext(user_id, role∈{admin,user}, allows_shared)` (`orchestrator/auth.py:51`). The access JWT carries `sub`, `role`, `allows_shared` — **no `aud`, no `client_id`, no device, no surface claim** (`auth.py:389`). Consequently **Web chat and the Search overlay are indistinguishable at the enforcement point**: both send an ordinary Lumogis JWT to overlapping `/api/v1/*` routes guarded by `require_user` (ADR-069/071; `routes/api_v1/memory.py:42`). The **only** surface-like boundary that exists is the `/mcp/*` path prefix, which runs a separate bearer gate and is the only place a per-request capability (`mcp:read`/`mcp:write`) lives — and those scopes are **write-gating only; read tools never gate** (ADR-130, `mcp_server.py:207`). There is no per-surface *read* gate anywhere in the system.

**Axis `sensitivity_class` — DOES NOT EXIST.** Entities carry `entity_type` (PERSON/ORG/PROJECT/CONCEPT/FILE + coding types — an extensible in-code `frozenset ENTITY_TYPES`, `models/mcp_write.py:63`), `scope`, and `memory_type`. There is **no per-entity sensitivity/PII/confidentiality tag**. "finance"/"health" exist only as free-text `context_tags` (`services/entities.py:60`). Text-stream secret/injection redaction exists (LUM-361/362 scanners) but classifies *streams*, not *entities*.

**Axis `role` — EXISTS, binary.** `admin` | `user` only; **`guest` is explicitly deferred** (ADR-112 §out-of-scope). The full matrix's third role lands with the LUM-334 guest follow-up.

**Axis `entity_type` — EXISTS.** Usable as a coarse sensitivity proxy in presets.

**Enforcement topology.** There is **no single physical chokepoint** — recall is enforced at ~20 call sites — but all household-scoped reads funnel through **one logical contract**: the `visible_filter` / `visible_qdrant_filter` / `visible_cypher_fragment` trio (`orchestrator/visibility.py`, mirrored in `services/lumogis-graph/visibility.py`). Every caller already threads a `scope_filter` argument. A CI gate (`test_no_raw_user_id_filter_outside_admin.py`) forces every household read through these helpers or a tagged escape hatch; a second gate (`test_phase3_grep_gate.py`) keeps the two mirror copies from drifting. **Two paths deliberately bypass the helpers** and must be handled explicitly: the fused MCP `recall` tool (`services/recall.py`, a personal-only `memories`/`banks` store, `# SCOPE-EXEMPT:`) and personal-only seed expansion (`entities.get_relations_for_seed`, `entities.py:567`). Chat injection funnels through one entry, `build_injected_context()` (`routes/chat.py:369`), which fans out to three independently-gated legs — session memory, auto-RAG docs, and KG entities over a cross-service HTTP `/context` hop.

**LUM-148 policy.md is advisory, not enforcement** (ADR-147 §25/70). It cannot be the enforcement store; enforcement must be code + a real table. The policy *wizard* can render/edit that table as a human-readable projection.

---

## Decision (recommended)

Adopt a **three-class sensitivity model + a small per-surface policy table + a single application-layer post-filter chokepoint**, delivered in **four sequenced phases** where Phase 1 (surface identity) is the hard prerequisite the rest depend on.

### 1. Sensitivity classes — coarse, derived, three values

Introduce `sensitivity_class ∈ { normal, sensitive, secret }`:

- **`normal`** — default; ordinary household knowledge.
- **`sensitive`** — finance, health/medical, legal, intimate/relationship, credentials-adjacent, and anything the user marks. Default-**hidden** on any surface not explicitly granted it.
- **`secret`** — never eligible for ambient/derived recall on *any* non-primary surface (default-deny fallback, Q4).

**Derivation (v1, no migration):** a function `sensitivity_class_for(entity)` computed from existing signals — a curated **sensitive `context_tags` allowlist** (`finance`, `financial`, `health`, `medical`, `legal`, `credential`, `intimate`, …) plus entity_type heuristics. Default `normal`. This reuses `context_tags` and avoids a per-entity column in v1.

**Promotion path (v2):** if households need per-entity override, add a nullable `sensitivity TEXT` column to `entities` (mirroring how `scope` was added in migration 013) and let LUM-131's contextual-integrity classifier populate it — replacing the coarse tag derivation with a learned classifier. Noted as the v2 upgrade, out of scope here.

### 2. Surface identity — extend the MCP-scope precedent (the prerequisite)

Introduce a `surface` dimension on the principal. Enumerate an allowlist constant `KNOWN_SURFACES` (mirroring `KNOWN_MCP_SCOPES`): `web_chat`, `search_overlay`, `mcp_recall`, `voice` (future, LUM-166), `ha_endpoint` (future, LUM-172).

- **JWT surfaces (Web, Search):** add a `surface` claim, minted per client. The Search overlay (ADR-069/071) mints/uses a `search_overlay`-tagged token; Web uses `web_chat`. This is the real auth change that unblocks the feature — **today the two are indistinguishable** and cannot be told apart without it.
- **MCP surface:** derive from the token — `mcp_recall`. The existing `request.state.mcp_scopes` ContextVar plumbing (`auth.py:750`) is the pattern to copy for a `request.state.surface`.
- **Marshalling across the KG hop:** `surface` (and `role`) must ride in the KG `ContextRequest` payload so `select_context_entities` and the ego/mention expansions apply the same rule KG-side (the orchestrator passes only `user_id` today).
- **Default/unknown surface → most-restrictive** (default-deny): unknown surface sees only `normal` `shared`/`system`, never `sensitive`/`secret`, never `personal`.

`UserContext` gains `surface: str | None` (defaulting to the deny-safe unknown).

### 3. Policy storage — a separate `surface_exposure_policy` table (design Q1)

**Recommendation: a dedicated table, NOT extending ADR-015 scope columns, NOT policy-file-driven.**

- ADR-015 `scope` is a **per-entity ownership** axis. Surface policy is a **small per-`{sensitivity_class × surface × role}` config matrix** — the wrong thing to smear across every entity row.
- LUM-148 policy.md is advisory-only, so it cannot gate; the wizard edits the table and renders it into policy.md as human-readable intent.

```sql
CREATE TABLE surface_exposure_policy (
  surface           TEXT NOT NULL,   -- KNOWN_SURFACES
  role              TEXT NOT NULL,   -- admin | user (| guest, LUM-334)
  sensitivity_class TEXT NOT NULL,   -- normal | sensitive | secret
  exposed           BOOLEAN NOT NULL DEFAULT FALSE,   -- DEFAULT-DENY
  PRIMARY KEY (surface, role, sensitivity_class)
);
```

Bounded cardinality (~5 surfaces × 3 roles × 3 classes ≈ 45 rows). **Absence of an `exposed=TRUE` row means hidden** — the table encodes allow-list exceptions over a deny baseline. `secret` has no `exposed=TRUE` rows on non-primary surfaces by construction.

### 4. Enforcement chokepoint — one application-layer post-filter (design Q2)

**Recommendation: a single logical chokepoint implemented as a post-filter, not a per-store WHERE predicate.**

Add `filter_by_surface_exposure(rows, *, surface, role)` (mirrored orchestrator + KG service, drift-gated like `visibility.py`). It looks each row's `sensitivity_class_for(...)` up against `surface_exposure_policy` and drops rows without an `exposed=TRUE` grant. Apply it at **every recall return site**, including the two helper-free bypass paths.

Why post-filter rather than extending the `visible_*` predicate:

1. **Store-uniform** — one function covers Postgres rows, Qdrant hits, and Cypher results identically; a WHERE-predicate approach needs three dialects and a queryable per-entity sensitivity column that does not exist in v1.
2. **Covers the bypass paths** — `services/recall.py` (all four legs + hydrate) and `get_relations_for_seed` never touch `visible_*`; a post-filter wraps their outputs too.
3. **Fail-safe by construction** — default-deny lives in one function; a new/unknown surface or class returns hidden.
4. **No migration** — works off derived `sensitivity_class`.

Cost: over-fetch (fetch-then-drop). Acceptable at household result-set sizes. **v2 efficiency path:** once a materialised `sensitivity` column exists (§1 promotion), push the predicate into the `visible_*` helpers for the hot chat legs. Noted, deferred.

**New CI gate:** `test_recall_sites_apply_surface_exposure.py`, analogous to the `user_id` gate — every recall return site must call `filter_by_surface_exposure` or carry an explicit `# EXPOSURE-EXEMPT:` tag (e.g. admin/audit routes, export).

### 5. Presets — bundles that seed the table (design Q3)

Ship named presets so households never hand-edit 45 rows (the HA one-by-one anti-pattern the compass note calls out):

| Preset | Applies to | Effect |
|---|---|---|
| `trusted_full` | `web_chat`, authenticated member | matrix is a no-op — the ADR-015 scope rule stands unchanged (back-compat) |
| `voice_kitchen` | `voice` in shared space | `normal` shared/system + own `personal`; **`sensitive`/`secret` hidden** (finance/health never spoken aloud) |
| `child_client` | thin client, child profile | `normal` shared/system only; no `personal`, no `sensitive`/`secret` |
| `guest_read_only` | `guest` role (LUM-334) | `normal` `shared`/`system` only |

`trusted_full` on `web_chat` guarantees **zero behaviour change** for the primary surface at ship: the matrix only ever *subtracts* from what the scope model already allows, and only on non-primary surfaces.

---

## Alternatives considered

- **Extend ADR-015 `scope` columns with exposure flags** — rejected: conflates per-entity ownership with a per-class×surface config matrix; would require an exposure column on every entity and every store.
- **Policy-file-driven enforcement (LUM-148 policy.md)** — rejected: policy.md is advisory by decision (ADR-147); enforcement needs a queried table. The wizard *renders* the table, it does not *replace* it.
- **Per-store WHERE predicate in the `visible_*` helpers** — rejected for v1: needs three query dialects and a materialised sensitivity column, and still misses the two helper-free bypass paths. Retained as the v2 efficiency upgrade once the column exists.
- **Per-entity ACL per surface** — rejected: same oversizing ADR-015 rejected for principals; households have ~5 surfaces, not thousands.
- **Heavy per-entity sensitivity classifier now (LUM-131)** — deferred: coarse tag-derivation ships without a migration; the classifier is the v2 populator of the promoted column.

---

## Dependency list (acceptance)

- **Hard prerequisite (new work):** surface identity — the `surface` JWT claim + Search-overlay-tagged token + `UserContext.surface` + KG `ContextRequest` marshalling. **Nothing in the matrix is enforceable until Web and Search are distinguishable.** Extends the ADR-130/LUM-531 MCP-scope machinery.
- **Blocks LUM-166 (voice):** a voice surface must not speak `sensitive` entities aloud; the `voice` surface identity + `voice_kitchen` preset must exist before voice ships. Hard blocker.
- **Informs LUM-172 (Home Assistant):** HA endpoints are surfaces; the preset model *is* the HA opt-in-exposure pattern.
- **Complements LUM-157:** publish/unpublish is the *who/scope* axis; this is the *where/surface* axis. Orthogonal, composable.
- **Distinct from LUM-131:** LUM-131 is Allow/Ask/Block for **actions**; this is **recall/read** gating. LUM-131's classifier later upgrades the sensitivity axis (§1 v2).
- **Completes with LUM-334 guest role:** the `role` axis's third value; the table's `role` column is ready for it.
- **Relates to LUM-586:** graph-aware shared entities are exposure candidates; the post-filter runs after their projection.

---

## Test-plan sketch — two-user × two-surface isolation

Mirror the ADR-015 store-symmetry discipline: prove a hidden entity leaks via **no** recall leg.

1. **Sensitive-on-voice hidden.** Alice marks a `finance`-tagged entity `shared`. Bob's `web_chat` (`trusted_full`) recalls it; Bob's `voice_kitchen` recall does **not**. Assert on all three legs of `build_injected_context` (session memory, auto-RAG, KG `/context`).
2. **Child/guest exclusion.** A `shared` entity tagged `sensitive` (e.g. surprise-party planning) is visible on Alice's `web_chat`, hidden on `child_client` and `guest_read_only`.
3. **Default-deny on unknown surface.** A request whose `surface` is unset or not in `KNOWN_SURFACES` sees only `normal` `shared`/`system` — never `sensitive`/`secret`/`personal`.
4. **Store-symmetry / no-leak.** For a hidden entity, assert exclusion via Postgres exact-name (`visible_filter` sites), Qdrant semantic (`visible_qdrant_filter` sites), Cypher traversal (`visible_cypher_fragment` sites), **and** the two bypass paths — fused MCP `recall` (`services/recall.py`) and `get_relations_for_seed`. This pins that the post-filter wraps every leg.
5. **MCP surface split.** MCP `recall` with an `mcp_recall`/`search_overlay`-surface token hides a `sensitive` entity that a `web_chat` token would return.
6. **Back-compat.** With `trusted_full` on `web_chat`, the full existing ADR-015 household-sharing suite (`test_household_sharing.py`) passes unchanged — the matrix subtracts nothing on the primary surface.
7. **CI gate self-test.** A deliberately unguarded recall return site fails `test_recall_sites_apply_surface_exposure.py`.

---

## Revisit conditions

- **Per-entity sensitivity override** consistently requested → promote the derived class to a materialised `entities.sensitivity` column (migration) and fuse the predicate into `visible_*` for the hot chat legs (v2 efficiency + accuracy).
- **LUM-131 contextual-integrity classifier lands** → it becomes the populator of the sensitivity axis, replacing the coarse `context_tags` allowlist.
- **Guest role (LUM-334) ships** → wire the `guest` role rows + `guest_read_only` preset; extend the RBAC enforcement matrix test.
- **A new surface is added** (voice, HA, or another client) → it inherits default-deny automatically; only presets/policy rows opt it in. No code change to the chokepoint.
- **Fused-recall store gains `scope`** → revisit whether `services/recall.py` should route through `visible_*` + exposure predicate rather than the post-filter wrapper.

## Status history

- **2026-07-14:** Draft created by `/explore LUM-559` (Opus 4.8). Three parallel codebase surveys established: surface identity and sensitivity class do not exist today; `visible_*` is the single logical (not physical) recall contract with two documented bypass paths; MCP scopes are write-only. Recommendation: three-class derived sensitivity + `surface_exposure_policy` table + application-layer post-filter chokepoint, phased behind a surface-identity prerequisite. Awaiting review before planning.
