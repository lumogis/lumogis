# ADR-170 — Plugin permission, sandbox & signing model for the open ecosystem (LUM-507)

**Status:** Draft (design) — pillars **(a)** and **(b)** implemented (**ADR 171** / LUM-612, **ADR 172** / LUM-613 on `dev` @ 2026-07-16); pillar **(c)** signing pending LUM-614
**Created:** 2026-07-14
**Decided by:** `/explore` (Opus 4.8)
**Linear:** [LUM-507](https://linear.app/lumogis/issue/LUM-507) (parent LUM-78; project Capabilities / Plugins; milestone v1.3; Urgent — "critical, non-deferrable")
**Builds on:** ADR-005 (plugin boundary), ADR-010 (ecosystem plumbing), ADR-028 (control surfaces), ADR-024 (connector permissions), ADR-042 (export boundary), ADR-153 (egress guard), ADR-136 (entitlement JWS), ADR-169/LUM-41 (invoke contract)
**Feeds:** LUM-171 (marketplace trust gradient), LUM-241 (author contract)

---

## Context

Per ADR-101, the **official registry is Lumogis's trust moat**, and LUM-507 owns the security substrate that must exist *before* the ecosystem opens — because a plugin runs against a household's **entire private dataset**. The ticket bundles three pillars: **(a)** per-plugin permission/grant with least-privilege, **(b)** sandbox + egress constraint so a malicious plugin can't silently exfiltrate, **(c)** signing/verification for the official/verified/community trust gradient.

A survey of the current substrate shows all three are far from done — and, critically, that **the entire current trust model assumes trusted first-party extensions.** ADR-005/010/028 each say so explicitly ("untrusted arbitrary code in Core," "public marketplace," "untrusted third-party plugins" are all out of scope today). Opening the ecosystem is therefore not "add a permission check" — it is a **trust-model shift** that needs an architectural decision first.

### Current state per pillar (survey)

**(a) Permission/grant — ~10%.** `CapabilityManifest.permissions_required: list[str]` exists but is **inert** — its only consumer maps a capability to a *connector name* (`unified_tools._permission_connector_for`); it never gates execution, and every shipped manifest declares `[]`. The real enforcement engine is Ask/Do: `permissions.check_permission(connector, action_type, is_write, user_id)` over the `connector_permissions` table (ADR-024) — but it's **binary ASK/DO per (user, connector)**, no scopes. Capability tools *do* flow through it at connector granularity, and ADR-024 already reserves a non-breaking `scopes TEXT[]` extension. So there's a real primitive to extend, but zero scope enforcement today.

**(b) Sandbox/egress — a real primitive, wrongly scoped.** `egress_guard.py` (`tethered.scope()`, PEP-578) is built (LUM-553 **Done**) but is **opt-in (default off) and wraps only Core's *LLM* calls.** In-process plugins are loaded by `load_plugins()` via `importlib.import_module` — **full-privilege Python inside Core, zero sandbox** (ADR-005: "structural, not policy-enforced"). A plugin/hook handler runs *outside* any `scope()` and can open a socket to anywhere — **the exfiltration path is completely open.** OOP capabilities are process-isolated, but `egress_guard` doesn't touch them, and the LUM-43 compose-policy guard (Done) only withholds Core DB creds from **non-allowlisted** services — the first-party `lumogis-graph` service is allowlisted and *does* hold full Postgres creds. ADR-153 explicitly says LUM-507 plugin egress "must compose via `scope()` intersection" = net-new.

**(c) Signing/verification — 0% in `lumogis-app`.** No plugin/manifest signature verification, no embedded Core public key, no entitlement/licence verifier. The Ed25519 JWS + JWKS rail exists **only issuer-side in the separate `lumogis-cloud` repo** (ADR-136); the Core-side offline verifier (LUM-96), the embedded public key + key ceremony (LUM-510), the runtime gate (LUM-262), and any manifest `signature` field are **all net-new**. Furthest from done.

---

## Decision (recommended)

**Split LUM-507 into three sequenced child deliverables behind one architectural decision, and ship a "community (sandboxed, unsigned)" tier from (a)+(b) *before* the "official/verified (signed)" tier (c).**

### 0. Architectural decision (do first, cheap): untrusted plugins are OOP-only

**In-process Python plugins remain first-party-only, forever.** They are unsandboxable in-process (full Core privileges), and ADR-005/028 already treat them as trusted. **The open ecosystem is OUT-OF-PROCESS capabilities only** (the ADR-010/169 HTTP-contract model): process isolation, no Core DB creds, egress-constrained, and — for the official tier — signed. This is the single decision that makes the rest tractable: we never try to sandbox arbitrary third-party code *inside* Core; we require it to live behind the capability HTTP contract. The trust gradient:

| Tier | Delivery | Trust basis |
|---|---|---|
| **Official** | first-party in-process plugin *or* signed OOP capability | code review + signature |
| **Verified** | signed OOP capability (third-party) | signature over a reviewed manifest |
| **Community** | unsigned OOP capability | sandbox-hard + explicit "UNVERIFIED" gate |

### (a) Permission/grant — extend Ask/Do to scopes; enforce `permissions_required`

Add the ADR-024-reserved `scopes TEXT[]` to `connector_permissions`; at the `check_permission` chokepoint, enforce the capability's declared `permissions_required` (from the LUM-41/ADR-169 manifest) against the user's **granted** scopes — least-privilege default (a scope not granted is denied). A grant UI/API lets the user see and approve exactly what a capability asked for at install. **Soft-depends on LUM-41 (ADR-169)** — enforcement is meaningless until the contract declares *what* to enforce (scopes, `is_write`, `auth`). Reuses the LUM-355/ADR-163 capability-derived floor for grant defaults.

### (b) Sandbox/egress — compose `scope()`, withhold creds, make it mandatory for community

Three moves, all extending existing primitives: **(i)** community OOP capabilities are **never** added to `compose_core_allowlist.txt` (no Core DB creds — LUM-43 already enforces this for non-allowlisted services); **(ii)** compose `tethered.scope(allow=…)` around **capability invocation and plugin/hook/tool execution** (the "intersection" ADR-153 names), not just LLM calls, and make it **mandatory (not opt-in) for the community tier**; **(iii)** constrain a community capability's declared `external_endpoints` (LUM-355/ADR-163) as its egress allowlist. Note the honest ceiling ADR-153 records: PEP-578 hooks are bypassable via C-extensions/ctypes — so belt-and-braces is OS/container network policy for the OOP process, not just in-Python `scope()`.

### (c) Signing/verification — ride the entitlement rail; ship last

Reuse ADR-136's Ed25519 JWS/JWKS rail: add a `signature` block to the manifest (the ADR-169 revisit item), Core offline-verifies it against an **embedded public key** (LUM-510) to grant the "official/verified" badge. **Hard-depends on LUM-510 (key ceremony + embedding) + LUM-96 (offline verifier).** Because this rides the *commercial-licensing* key infrastructure (shared with entitlements — LUM-262/263), it cannot be built independently of that work. **The community tier (a)+(b) ships without it**; (c) only *adds* the verified tier and the paid official-connector story.

---

## Interdependency map (the crux)

```
                         LUM-507 (this ADR — decompose into a/b/c)
                              │
  ┌───────────────────────────┼───────────────────────────────┐
  │ (a) permission/grant      │ (b) sandbox/egress            │ (c) signing/verify
  │                           │                               │
  ├─ LUM-41/ADR-169 ★ soft-dep ├─ LUM-553 egress guard ✅ DONE  ├─ LUM-510 signing keys ⛔ Backlog
  │  (declares scopes/is_write)│  (but LLM-only → compose new) │  (key ceremony + embed)
  ├─ ADR-024 Ask/Do ✅ (extend  ├─ LUM-43 compose guard ✅ DONE  ├─ LUM-96 offline verifier ✅ design-Done
  │  scopes TEXT[])           │  (no DB creds for non-allow.) │  (net-new build)
  └─ LUM-355/ADR-163 ✅ (floor  └─ LUM-355/ADR-163 ✅            ├─ LUM-263 issuer JWS (lumogis-cloud)
     for grant defaults)        (external_endpoints = egress) ├─ LUM-262 runtime gate ⛔ Backlog
                                                              └─ ADR-136 JWKS rail ✅ (issuer-side)
  Orthogonal / already covers inbound: LUM-362 tool-result injection scan ✅ DONE (NOT exfiltration)
  Downstream consumer: LUM-171 marketplace (needs the (c) trust gradient) · LUM-241 (documents (a) declarations)
```

**Key reads:**
- **LUM-507 is unblocked.** Its only formal blocker (LUM-553 egress guard) is **Done**; readiness audit (LUM-238) and auth hardening (LUM-42) are Done. It is plannable now.
- **Pillars (a) and (b) can ship the community tier now** — both extend real primitives (Ask/Do scopes; `tethered.scope()` + compose guard). (a) soft-depends on LUM-41/ADR-169 landing so there are declared scopes to enforce.
- **Pillar (c) is entangled with the commercial-licensing key rail** (LUM-510 → LUM-96/262/263, ADR-136). It's the furthest from done and gates only the *verified/official* tier — so it must be sequenced **last**, not treated as a prerequisite for opening a sandboxed community ecosystem.
- **LUM-362 is a red herring for exfiltration** — it scans *inbound* tool results for injection; it does nothing about a plugin sending data *out*. That's pillar (b), which is wide open today.

---

## Alternatives considered

- **Sandbox in-process plugins (seccomp/subinterpreters/RestrictedPython).** Rejected: in-process = full trust by construction; every containment is bypassable and fragile. Requiring OOP for untrusted code is the sound boundary (ADR-028 already leans this way).
- **Ship signing (c) first as the gate.** Rejected: it's the furthest from done, cross-repo, and blocks a sandboxed community tier that (a)+(b) can deliver independently. Sandbox-hard-unsigned is a legitimate, shippable trust tier.
- **A full policy engine (Cedar/OPA) for grants now.** Deferred per ADR-024 — extend Ask/Do with `scopes TEXT[]` first; reach for a policy engine only if the scope space or principal count demands it.
- **Keep LUM-507 as one ticket.** Rejected: it conflates three deliverables with different readiness and dependencies; decomposing lets (a)+(b) proceed while (c) waits on LUM-510.

## Recommendation

1. **Adopt the OOP-only-for-untrusted architectural decision** (§0) — it's the cheap unlock.
2. **Decompose LUM-507 into three child tickets:** (a) capability permission/grant model, (b) capability sandbox + egress composition, (c) manifest signing/verification.
3. **Sequence:** land LUM-41/ADR-169 (contract) → (a) + (b) [community tier, shippable] → (c) after LUM-510 + LUM-96 [official/verified tier].
4. **Marketplace (LUM-171) can open at the community tier** once (a)+(b) ship; the signed/verified badge follows with (c).

## Test-plan sketch

- **(a):** a capability declaring `permissions_required=["memory:read"]` is denied `memory:write` at `check_permission` unless the user granted it; grant/revoke round-trips; least-privilege default (ungranted scope denied).
- **(b):** a community OOP capability with `external_endpoints=["api.foo.com"]` cannot reach `evil.com` (scope intersection); it holds no Core DB creds (compose-policy CI fixture); an in-process untrusted plugin is *refused loading* (OOP-only rule).
- **(c):** a manifest with a valid signature over an embedded `kid` verifies offline → "verified"; tampered/absent signature → "community/unverified" badge, never "verified"; key rotation grace window honoured.
- **Two-tier isolation:** a community capability cannot read another user's data or exfiltrate; official/first-party retains current access.

## Revisit conditions

- **LUM-510 + LUM-96 ship** → build pillar (c); add the `signature` manifest block (coordinate with ADR-169 revisit).
- **A real per-scope policy space emerges** (or >~20 principals) → revisit Cedar/OPA over the Ask/Do+scopes model (ADR-024).
- **A container/OS network-policy layer is adopted** → make it the primary egress boundary and demote `tethered.scope()` to defense-in-depth (per ADR-153's own bypassability note).

## Status history

- **2026-07-14:** Draft created by `/explore LUM-507` (Opus 4.8) + substrate survey. Findings: current trust model assumes trusted first-party extensions; in-process plugins are full-privilege/unsandboxed; the exfiltration path is wide open; `permissions_required` is inert; signing is 0% in-repo and rides the cross-repo entitlement key rail. Recommends the OOP-only-for-untrusted decision + a three-pillar decomposition, shipping a sandboxed community tier (a+b) before the signed official tier (c, gated on LUM-510/LUM-96). LUM-507 is unblocked (LUM-553 Done). Awaiting review before planning.
- **2026-07-16:** Pillars **(a)** and **(b)** shipped to `dev` via LUM-612 / LUM-613 (`/record-retro` → **ADR 171**, **ADR 172**). Parent LUM-507 remains open for pillar **(c)** (LUM-614) and hard container egress (LUM-618).
