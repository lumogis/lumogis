# ADR-102: Repo topology & cloud-services boundary for the commercial/ecosystem era

**Status:** Proposed — audit + recommendation. **No structural change made.** Captures the all-angles audit triggered by ADR-101; ratify before any repo is created.
**Created:** 2026-06-15
**Decided by:** structural audit dialogue (operator-guided, 2026-06-15)
**Builds on:** ADR-042 (KG public/private export boundary — strip over split), ADR-101 (commercial & ecosystem model), ADR-037 (GHCR publish from public repo only), ADR-093/094 (Core debundle / Server)

> **Scope.** *Where does the code live, and how do we structure ourselves to deliver ADR-101* — repos, the public/private boundary, cloud-operated services, secrets, CI, and licensing. Decision/audit artefact only; no implementation, no repo created. Operator steer recorded: the new repo is **greenfield** and **relay-centric** (entitlement issuance + registry backend ride along as operated services); **mobile clients stay in the product repo**.

## Context

ADR-101 moved Lumogis from a single-moat model (KG) to a trust + ecosystem model whose non-AGPL surface is **fragmented** across components with very different lifecycles, deploy targets, audiences, and blast radius. The current structure was sized for *one* proprietary surface tightly coupled to Core. This ADR audits whether that structure must change.

**Current topology (as-is, verified 2026-06-15):**

| Repo / line | Role |
|---|---|
| **`lumogis-app`** | The product. **One** repo holding **both** AGPL Core and the proprietary surfaces; separated only at **export time**. Proprietary today = KG (`services/lumogis-graph/` + graph plugin/adapters/premium composes) and Lumogis Server (`apps/lumogis-server/`). Clients in `clients/` (`lumogis-web`, `lumogis-search`). |
| **`lumogis-devtools`** | Product OS, skills, Linear, `.cursor/` (symlinked into the app). |
| **`lumogis-public` / `upstream/main`** | AGPL **export snapshot** — *different lineage* from private `main`. |
| **`lumogis-site`** | lumogis.ai marketing — **Cloudflare Workers static assets** (`wrangler`, serves `./public`; no server-side code). Newsletter via Brevo + Turnstile; pricing page (v1.3) = Stripe Payment Links. |

**Pipeline (two promotions, never a raw push):** `dev` → private `main` (scoped RC, `prepare-private-release-from-dev`) → public export (`create-upstream-export-tree.sh` + `public-export-strip-list.txt` deny-list + `check-public-export.sh`, synced into `lumogis-public`).

**The structural fact that drives this audit:** the export is a **deny-list** — it ships *everything except* listed paths, so it **fails open**. Every proprietary surface added to `lumogis-app` raises the odds of an accidental AGPL leak, and the worst possible leak — an **entitlement/plugin signing private key** — must never be one strip-list typo away from publication.

**Greenfield confirmed:** no relay, cloud, Stripe, billing, entitlement, tunnel, or signing-key code exists in `lumogis-app` today. The new repo is net-new; nothing is extracted or migrated.

## Recommendation

**Add exactly one repo; keep everything else as-is. The product→export pipeline does not change.**

1. **Create `lumogis-cloud`** — all Lumogis-**operated** services and their secrets: the **relay** (primary), the **entitlement issuance / Stripe webhook / JWKS / revocation** service, and the **plugin registry backend**. Private, never exported.
2. **Keep the product strip-model** for shipped proprietary code (KG, official connectors, official Server/mobile builds). **Do not split** them into their own repo — ADR-042 already weighed strip-vs-split and the reasoning holds.
3. **Mobile clients live in `lumogis-app/clients/`** (e.g. `clients/lumogis-mobile`), AGPL like `lumogis-search`; the official **signed** build is the convenience, not a separate repo.
4. **Plugin SDK / Extension Contract (LUM-241) stays in the AGPL export** for now; graduate to a standalone public `lumogis-sdk` repo only if external-developer traction demands independent versioning.
5. **No change** to `dev`→`main`→public, to `lumogis-devtools`, or to `lumogis-site`.

Net: ~4 repos → ~5; only **one** is new; the export pipeline and its gates are untouched.

## The angles

### A. Repo topology (target)

| Repo | Contains | Ships to user? | Exported? |
|---|---|---|---|
| **`lumogis-app`** | AGPL Core, clients (web/search/**mobile**), community connectors, plugin SDK/contract source; **+ stripped:** KG, official connectors, official Server build | Yes (AGPL) + Yes (proprietary builds) | AGPL parts only |
| **`lumogis-public`** | AGPL export snapshot | — | is the export |
| **`lumogis-cloud`** *(new)* | **relay**, entitlement issuer/Stripe/JWKS/revocation, registry backend, **signing keys** (in secret manager) | **No — operated only** | **Never** |
| **`lumogis-devtools`** | Product OS / skills | No | No |
| **`lumogis-site`** | Marketing (Cloudflare Workers static assets) | served as site | No |
| *(future)* **`lumogis-sdk`** | Public plugin contract/types | Yes | n/a |

### B. The relay (the heart of the new repo)

- **What it is:** a thin, always-on, end-to-end-encrypted **reverse-tunnel broker** between a client (away from home) and the user's home server (behind a router, no public address). **Forwards ciphertext, stores nothing**; decryption only at home. On the home LAN, clients connect **directly — no relay, no charge**. (ADR-101 §2; design feeds from LUM-218.)
- **Build-vs-buy (open):** *own* (Go/Rust tunnel — `frp`/`rathole`/WireGuard+coordination/`headscale`/`tsnet`) vs *orchestrate a provider* (Tailscale Funnel, Cloudflare Tunnel, ngrok). Trade-off: control/margin/privacy-story vs ops burden. Decide in LUM-506.
- **Deploy target:** a **persistent service** (VPS / fly.io / container host) — *not* serverless. This is the first always-on service Lumogis operates (see Angle I).
- **Secrets it holds:** relay/device auth tokens — **not** the entitlement signing key (that's the issuer's). Access is authorised by the entitlement (`remote_access`).

### C. Entitlement issuance & billing

- The Stripe webhook + signed-entitlement issuance runs as a **Cloudflare Worker in `lumogis-cloud`** (LUM-263) — **built there from the start**, not in `lumogis-site` and moved later. (Lumogis's entire web/serverless stack is Cloudflare + `wrangler`; the earlier Vercel sketch is dropped.)
- **Why it must not be near the export:** it holds the **entitlement signing private key**. A signing-key leak = anyone can forge any entitlement = the whole commercial model is void. Physical separation (own repo, own secret manager), not deny-list separation.
- The serverless issuer (Stripe webhook/JWKS — Cloudflare Workers) and the persistent relay can co-exist as **sub-packages of `lumogis-cloud`** with distinct deploy targets, or split later; one repo keeps the operated-services + secrets boundary coherent.

### D. Plugin registry & the signing trust anchor

- **Registry backend** (catalog, official/verified/community tiers — LUM-171) → `lumogis-cloud`. The catalog holds no household data.
- **Signing trust:** plugins are signed with a key held in `lumogis-cloud`; **Core embeds the public verification key** (safe — public keys only) to verify plugin + entitlement signatures, with `kid` rotation. This is the same offline-verify pattern as the entitlement gate (ADR-101 §9) and the security model is LUM-507.
- **Community plugins** live in external repos and point at the catalog; **official plugins** are the proprietary product surface (`lumogis-app`, stripped).

### E. CI & secrets inventory

| Secret / artefact | Lives in | Rule |
|---|---|---|
| Entitlement **signing private key** | `lumogis-cloud` secret manager | **Never** in `lumogis-app` or any export-building repo |
| Entitlement/plugin **public verify keys** | embedded in Core (`lumogis-app`, public) | Public — safe |
| Plugin **signing private key** | `lumogis-cloud` | Same as above |
| **Stripe** secret + webhook secret | `lumogis-cloud` | Operated only |
| Relay auth secrets | `lumogis-cloud` | Operated only |
| **Code-signing identity** (Apple/Windows, installers) | signing CI secrets (LUM-406) | Separate from export build |
| Private GHCR / registry tokens | CI | KG/official-connector images build from `lumogis-app` CI to private GHCR (LUM-261); public Core image from public repo (ADR-037) |

**Rule of thumb:** the repo that *builds the public export* (`lumogis-app`) must never hold a private *signing* key. Issuance/signing lives in `lumogis-cloud`.

### F. Licensing / AGPL boundary for new components

- **Relay, issuer, registry backend** are **separate works** (Lumogis-operated, not derived from Core, not distributed) → no AGPL obligation, provided they don't incorporate Core source. Keep them arm's-length (network/API only).
- **KG, official connectors** — proprietary plugins over the public contract (ADR-042 logic).
- **Mobile / Server** — AGPL-or-proprietary is an open product call (the *signing identity* is the asset either way; cf. ADR-093/Ardour pattern).
- **Legal review** needed on: relay data-handling, the AGPL boundary for proprietary plugins/relay, and the goodwill-licence/EULA (carried over from ADR-101).

### G. Data & privacy boundary (local-first must hold)

| Cloud service | Sees | Never sees |
|---|---|---|
| Relay | ciphertext in transit | plaintext, data at rest |
| Entitlement issuer | email + purchase event | household data |
| Registry backend | plugin catalog + which plugin you fetch | household data |

The local-first / non-SaaS invariant holds: no household data lands on Lumogis infrastructure. This is also the pricing-page trust story.

### H. Export safety — the deny-list fragility

- The deny-list **fails open**; the carve-out of `lumogis-cloud` removes the **catastrophic** class (signing keys / operated services can't leak because they're not in the repo).
- **Pressure-relief valves, in order:** (1) *now* — keep cloud + secrets out entirely (`lumogis-cloud`); (2) *later, only if proprietary shipped surfaces multiply past a handful* — flip the export to an **allow-list** (ship only listed paths) or stand up a private `lumogis-premium` repo. Don't pre-build (2); the trigger is "official connectors grow large / strip-list maintenance becomes error-prone."

### I. Operational reality

- The relay is the **first always-on service Lumogis operates** (today: a Cloudflare Workers static site + a planned Cloudflare Worker webhook, both stateless/managed). New muscle: uptime, scaling, bandwidth cost, abuse handling, incident response.
- The ongoing cost is exactly what makes the recurring charge honest (ADR-101 pricing principle) — and it's the concrete form of the **Persona-C-commitment** bet ADR-101 flagged: standing this up only pays off if Lumogis commits to the non-technical audience.

### J. Sequencing

- `lumogis-cloud` is greenfield — **no extraction from `lumogis-app`, no migration risk.**
- First content: relay (LUM-506) + entitlement issuer (relocated LUM-263/264) + registry backend (LUM-171). Build issuance *there*, not in the site repo.
- `lumogis-app` and the export pipeline are untouched until/unless official connectors trigger the Angle-H valves.

### K. `lumogis-cloud` is operationally *simpler* than `lumogis-app`

Because it is **never exported**, it has **no dev→main→public dance, no strip-list, no export gates** — just `main` + feature branches + CI to its deploy target. The new repo adds a service to operate, but not export complexity.

## What explicitly does NOT change

- The `dev` → private `main` → public-export pipeline and its gates.
- The strip-model for KG and shipped proprietary product code (ADR-042 stands).
- `lumogis-devtools`, `lumogis-site`, the GHCR split (ADR-037).
- Mobile stays in `clients/` (product repo), not the cloud repo.

## Consequences

**Easier / safer:**
- The catastrophic leak class (signing keys, operated services) is removed by *physical* separation, not deny-list discipline.
- Operated services get a deploy/secret/lifecycle home that matches how they actually run.
- The product repo and its battle-tested export pipeline are undisturbed.
- The new repo is simpler than the product repo (no export machinery).

**Harder / new:**
- A new repo + a new always-on service to operate, secure, and pay for.
- Cross-repo coordination: Core embeds public keys whose private halves live in `lumogis-cloud` (key-rotation discipline across repos).
- One more place secrets live (mitigated: it's the *right* place, and it's isolated from export).

## Open decisions (for ratification / follow-up explorations)

- Relay **build-vs-buy** (own tunnel vs provider) and deploy host — LUM-506.
- Is `lumogis-cloud` **one repo with sub-packages** (relay + issuer + registry) or split per service? (Lean: one repo, sub-packages, until scale says otherwise.)
- Mobile clients **AGPL vs proprietary** (signing identity is the asset either way).
- When (if ever) to flip the export to an **allow-list** or add `lumogis-premium` (Angle H trigger).
- Whether to stand up a public **`lumogis-sdk`** repo for the ecosystem contract, vs keep it in the export.

## Revisit conditions

- **Proprietary shipped surfaces multiply** → trigger the Angle-H valves (allow-list or `lumogis-premium`).
- **External plugin-dev traction** → split out `lumogis-sdk`.
- **Relay build-vs-buy** resolves toward "buy" → `lumogis-cloud` shrinks to orchestration + issuer + registry.
- **Legal review** of relay/AGPL/EULA returns constraints → amend boundaries.

## Status history

- 2026-06-15: Proposed via structural audit (operator-guided). Recommends one new repo (`lumogis-cloud`, relay-centric, operated services + secrets), keeping the product strip-model and the dev→main→public pipeline unchanged, mobile in `clients/`, and the plugin contract in the export for now. No repo created; ratify before acting.
- 2026-06-17: Amended for platform consistency — **Cloudflare confirmed as the web + serverless platform** (`wrangler` / Workers). The entitlement issuer / Stripe webhook / JWKS is a **Cloudflare Worker in `lumogis-cloud`** (the Vercel sketch in LUM-263 is dropped); `lumogis-site` is **Cloudflare Workers static assets** (serves `./public`, no server-side code; newsletter via Brevo + Turnstile). Relay build-vs-buy and deploy host remain open (LUM-509). No structural recommendation changed.
