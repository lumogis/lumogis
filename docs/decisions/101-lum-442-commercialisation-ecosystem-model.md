# ADR-101: Commercialisation & ecosystem model — trust moat, relay, open plugin ecosystem

**Status:** Accepted — canonical commercial model. **Builds on ADR-042** (KG ships proprietary/stripped; `GRAPH_MODE` default `disabled`; GraphStore Protocol stays the public contract) and refines the paid surface it named (graph + connectors + Lumogis Cloud relay). Supersedes the prior LUM-442 lean (Option A "KG-as-pillar / Pro = household") and the scattered framings in LUM-260 / LUM-96 / LUM-169. Implementation tickets re-scoped below; no code shipped yet.
**Created:** 2026-06-15
**Last updated:** 2026-06-15
**Decided by:** `/explore LUM-442` dialogue + operator decision (2026-06-15)
**Linear:** LUM-442 (exploration → this ADR is the canonical reference for the LUM-260 chain)
**Exploration:** `.cursor/explorations/LUM-442-commercialisation-and-licensing-model.md` (devtools; maintainer-local — rewritten in parallel with this ADR)

> **Scope.** Product-governance and commercial-boundary decision (free/AGPL vs paid). Decision artefact only; no implementation. This ADR is the single decision owner for the Lumogis commercial model and supersedes earlier, contradictory framings. Read it as the cornerstone the LUM-260 chain builds on.

## Context

LUM-442 set out to ratify a commercial model whose decisions were scattered across LUM-260 (implementation), LUM-96 (validation mechanism), and LUM-169 (managed-keys). The closest prior decision — **ADR-042** (LUM-242 / the LUM-238 ecosystem audit) — already locked the boundary: the KG ships **proprietary and stripped** (not AGPL), and the paid surface was named as **graph layer + MCP connectors + Lumogis Cloud relay**. ADR-101 is highly consistent with that and refines it. The first pass of *this* exploration, however, leaned toward **Option A**: the KG as a private, hard-gated proprietary image and the **primary recurring lever** that justifies the whole subscription, with "Pro" as a per-household licence.

That lean was pressure-tested in dialogue and **did not survive**. The decisive objections, in order:

1. **The KG is the wrong thing to gate.** People don't use a knowledge graph today, don't *miss* it when it's absent, it carries ongoing curation/dedup overhead, and its benefit accrues over months, not on first use. Industry data backs this: ~68% of people who adopt a PKM/graph tool abandon it within six months (Forte Labs), with "maintenance cost grows exponentially → tag chaos / orphan notes" as the cited cause. Building the recurring fee on the single feature with the highest churn and the lowest felt-absence is backwards. A subscription must clear "used now / missed when gone / instant value"; the KG fails all three.

2. **You cannot monetise a local feature, and you cannot monetise Persona A.** Every successful local-first comparable monetises the thing that is genuinely hard or costly to self-provide — **not** a local capability:
   - **Home Assistant / Nabu Casa** — €6.50/mo for **remote access + cloud voice + cloud backup**; everything local is free; this funds the foundation.
   - **Obsidian** — ~$25M ARR, 7 people: **Sync** ($4–8/mo) + **Publish/hosting** ($8–16/mo) + a commercial-use licence. Core free.
   - **Plex** — local playback free; **remote streaming** is the paywall (Plex Pass $7/mo, $70/yr, $250 lifetime). They *eliminated* the one-off mobile-app unlock in favour of the recurring remote-access charge.
   - **Onyx/Danswer** — free self-host (MIT); paid = managed cloud + SSO + support.
   - **Immich / PhotoPrism** — **optional** lifetime "support us, we won't lock you out" licences (Immich $99 server / $25 user). Goodwill, not a moat.
   - **Khoj** — pure-cloud play **walked back**; self-host is now primary.
   A FOSS-literate Docker self-hoster (Persona A) self-provides everything — Tailscale, Docker, BYO-keys, self-built binaries — and will pay for almost nothing. The paying customer is, by definition, **not** Persona A: it is Persona C and the large "capable but can't be bothered" middle that Plex Pass actually sells to.

3. **The code moat was the wrong kind of moat.** A private KG image is defensible as code, but the API is AGPL and open: a third party can build their own app + relay against it and sell access, and AGPL's network clause does **not** reach an independent client/relay that merely calls Core over HTTP. The durable moat for a local-first product that tunnels into someone's **private home** is **trust + the official default + the registry + execution** — none of which can be stripped into a private repo or signed into a binary. Every comparable above faces the identical clone exposure and thrives on exactly this non-code moat.

The shipped code already supports the resulting model with minimal rework: `GRAPH_MODE` defaults to `disabled` (so "KG off unless purchased" is the current default), `CapabilityLicenseMode` (`COMMUNITY`/`COMMERCIAL`) is the existing tier tag, and the strip-list already isolates `services/lumogis-graph/`, `apps/lumogis-server/`, and the premium surfaces.

## Decision

Lumogis is a **trust-and-execution business with an open plugin ecosystem**, not a code-moat business. The base product is free, AGPL-3.0-only (ADR-032), and local-first; revenue comes from things that cost Lumogis something ongoing to provide, plus optional goodwill. Concretely:

### 1. Free base product (AGPL, local-first, whole on its own)

- Core (orchestrator, ingest, folder search, Ask/Do, hooks, MCP), all clients (Web, Search overlay, future Desktop), folder watch, and **community connectors**.
- **Multi-user / household is FREE.** It lives in the AGPL Core (`AUTH_ENABLED`, per-user isolation, household credential scopes — ADR-012/018/024/027) and the family value comes from shared data, not from logins. **This reverses the earlier "multi-user = Pro" reading.** There is **no licence gate on `POST /api/v1/admin/users`** — drop that mechanic entirely.
- A **self-built** Lumogis Server (build-from-source / Docker) is free. The base product is complete and useful with **zero** paid component and **zero** cloud touchpoint.

### 2. The cloud — exactly one operated touchpoint, and it stores no user data

Lumogis operates **one** cloud service plus a thin issuance endpoint. It is **not** a SaaS and it does **not** host user data:

- **The relay** (the revenue engine — see §3). A dumb, end-to-end-encrypted reverse tunnel that brokers a connection between a client (phone/laptop on 4G or foreign WiFi) and the user's home server, which sits behind their router with no public address. The relay **forwards encrypted traffic and stores nothing**; data is decrypted only at home (the Nabu-Casa model). On the home LAN, clients reach the server **directly with no relay and no charge**.
- **Licence/entitlement issuance** (thin function, offline-verifiable — see §9). Stripe event → signed entitlement. No household data touches it.

**Explicitly ruled out: data-hosting cloud** (Proton-style E2E storage, hosted-photo style). It is a capital-intensive, low-margin commodity competing with Google/Apple/Proton on price *and* trust, and it contradicts the local-first, non-SaaS positioning. Not a Lumogis business.

### 3. Revenue lines, and the pricing principle

**Pricing principle: charge recurring only where Lumogis carries a recurring cost; charge one-off (or not at all) for pure-local capability.** This pre-empts the "why am I renting something that runs on my own hardware?" objection that kills local-feature subscriptions.

| Line | Charge | Why it is honest |
|------|--------|------------------|
| **Remote access** ("Lumogis Remote": reach your server from anywhere via the relay + official mobile/Web apps) | **Subscription** (~€5–7/mo; lifetime option viable, cf. Plex $250 / Immich lifetime) | Lumogis runs a relay 24/7 and pays its bandwidth |
| **Official connectors** (maintained, signed Gmail/Drive/Notion/etc. bundles) | **Subscription** | They break as third-party APIs change; Lumogis carries the upkeep |
| **Knowledge Graph add-in** | **One-off purchase** | Runs entirely on the user's hardware; zero ongoing cost to Lumogis |
| **Persona-A goodwill licence** | Optional one-off / lifetime | Pure "support the project"; restricts nothing |

The **primary** lever is remote access; connectors are the natural second line the ecosystem creates; the KG is **gravy, not a pillar**; the goodwill licence captures Persona A without restricting them. The relay/apps + no-Docker official installer are bundled into the remote-access offering — the installer is **not** a separate SKU and **not** a feature gate (front-loading the paywall before first value, as Plex learned, loses to recurring remote access).

### 4. Knowledge Graph — optional paid add-in, off by default

- **Lumogis works fully without the KG.** It ships **off** by default (`GRAPH_MODE=disabled` — already the default, so **no code change** to the default). The base product is whole without it.
- The KG is an **optional add-in purchased from the store**, unlocked via the same entitlement rail as paid connectors (§9). **One-off purchase**, not a subscription (§3).
- It remains a **proprietary plugin** (`CapabilityLicenseMode.COMMERCIAL`; `services/lumogis-graph/` stays strip-listed). The earlier "default-on, free, open KG" idea is **superseded** by "off by default, optional paid add-in" — the agreed final form.
- It is **not** the pillar and the business does **not** depend on its conversion. Price expectations accordingly: a slow-burn add-in for power users.

### 5. Connectors — community free, official maintained = paid

- **Community connectors:** free, open, anyone can build and publish (see §6).
- **Official connectors:** maintained and signed by Lumogis, sold as a **subscription** (the upkeep moat). They live as proprietary/strip-listed plugins delivered through the entitlement rail.

### 6. Open plugin ecosystem (the browser-extension model)

- **Open API; community plugins and clients.** Third parties build plugins and clients against a stable, documented Core API, like browser extensions. This deepens the moat (more plugins → more useful → more users → bigger registry → deeper default/trust lock-in). The win, as with the Chrome Web Store, is **owning the registry**, not selling the extensions.
- **The official registry is the trust gateway** — official/verified/signed plugins vs. raw community plugins — and is Lumogis's to control. This *is* the moat surface (§7).
- **Plugin permissions, sandboxing, and signing are the critical, non-deferrable design work.** Plugins run against a user's entire private home dataset; an unconstrained plugin model is the browser-extension security nightmare applied to someone's home. Required before opening the ecosystem: a per-plugin permission/grant model, a sandbox that prevents silent read-everything-and-exfiltrate, and signing/review for the official registry. This work is *also* what makes "official/verified" a meaningful trust differentiator (and underpins the paid official-connector line). Builds on ADR-005 (plugin boundary), ADR-010 (ecosystem plumbing), ADR-028 (self-hosted extension architecture).

### 7. The moat — trust + official default + registry + execution (not code)

The durable moat is being **the official, trusted Lumogis that just works**: official signed apps, the relay people trust to tunnel into their home, the official registry, and continued execution. A cloner can replicate the relay protocol and call the open API, but cannot replicate trust-for-private-home-data, app-store default presence, the official registry, or roadmap control. This moat is **soft** — defended by execution and trust, not by law or secrecy — so it must be continuously earned: a mediocre or overpriced official offering can lose share to a better third party. Accept this; it is the same position Plex, Home Assistant, and Tailscale hold and win from.

### 8. Persona mapping

- **Persona A (FOSS-literate, self-hosts everything):** uses the free base + their own Tailscale/reverse proxy for remote access; never pays for the relay. Captured (optionally) by the **goodwill licence**. Not the revenue base — by design.
- **Persona C + the "capable but can't be bothered" middle:** pay for **remote access** (relay + official apps + no-Docker installer), **official connectors**, and optionally the **KG add-in**. This is the revenue base, and it only exists if Lumogis commits to a genuinely no-terminal, one-click, it-just-works product. **Monetisation viability is contingent on that commitment** (see Revisit conditions).

### 9. Entitlement / licence mechanism

- **One generic entitlement rail** serves all paid items (relay subscription, official connectors, KG add-in). Building it once for connectors means the **KG is just another SKU on the same rail** — the bespoke per-customer GHCR-image KG gate from the original LUM-260 plan does **not** return.
- **Offline Ed25519-signed entitlements (JWS)**, public key embedded in the verifier; claims `plan`, `instance_id`, `entitlements[]`, `expires`, `issued_at`, `kid`. **Per-instance, never per-seat** (per the vision's anti-per-seat stance). Verify locally; soft daily online refresh with a generous grace window; on lapse, **degrade gracefully, never delete data** (subscriptions revert to local-only / read-only; one-off add-ins like the KG remain owned). Build-vs-buy (in-house Ed25519 vs keygen.sh / self-hosted Paycheck) is a LUM-263/96 implementation question, unchanged by this ADR.

## What this supersedes / changes

- **Supersedes** the prior LUM-442 lean (Option A KG-as-pillar; "Pro = per-household licence"; multi-user = Pro). The new primary lever is **remote access**, the moat is **trust/ecosystem**, and **multi-user is free**.
- **Refines, does not reverse, ADR-042** (LUM-242 / LUM-238): the KG, connectors, and a Lumogis Cloud relay remain the paid surface, and the KG stays proprietary/stripped. What changes: the KG is demoted from *pillar* to an **optional one-off add-in (off by default)**, the **relay becomes the primary lever**, multi-user is **free**, and the moat is relocated from a KG code-gate to trust + ecosystem. The only thing superseded is the **mid-dialogue "default-on, free, open KG" idea** — the agreed final form is **KG off by default, optional paid one-off add-in**.
- **Reconciles** the "Lumogis Server free vs paid packaging" question (Lever 3 in the exploration): the no-Docker official installer is a **bundled convenience inside the remote-access offering**, not a separate SKU; self-build stays free; `apps/lumogis-server/` stays strip-listed.
- **Confirms** managed-keys (LUM-169) as a separate optional add-on, **deferred past v1.x** — revisit only if Persona-C demand is proven.
- **Confirms** v1.0/HN is search-first and sells nothing; the commercial surface is a later gate, not a launch blocker.

## Code & strip-list posture

Largely **unchanged** — a strength, since most of the shipped open-core split holds:

- **Stays public/AGPL:** Core, all clients, folder search, community connectors, multi-user/household.
- **Stays strip-listed/proprietary:** `services/lumogis-graph/` (paid KG add-in), `apps/lumogis-server/` (official build), official paid connectors, the premium compose files, and the graph ADRs (007/008/009/011/035).
- **`GRAPH_MODE` default `disabled`** is correct as-is (KG off until purchased).
- **`CapabilityLicenseMode.COMMERCIAL`** stays the tag for KG + official connectors.
- **New, net:** the relay service + official mobile apps; the plugin **registry** + **permission/sandbox/signing** model; the generic **entitlement rail**. The bespoke KG runtime-licence gate (402-on-invalid private image) is **dropped** in favour of the entitlement rail.

## Re-scope of blocked tickets

| Ticket | Was | Now |
|--------|-----|-----|
| LUM-260 (KG licensing & payment, parent) | KG private-image gate + per-household Pro | Generic **entitlement rail**; KG is one SKU on it, not the pillar |
| LUM-262 (runtime KG licence gate, 402) | Bespoke KG JWT gate | Folded into the entitlement rail; KG verifies an `entitlements[]` claim like any paid plugin |
| LUM-263 (Stripe webhook + JWT issuance) | Issues household Pro JWT | Issues per-instance entitlement JWT (relay sub, connector sub, KG add-in) |
| LUM-264 (post-purchase delivery) | GHCR token + key per customer | Entitlement + store-mediated plugin delivery; no per-customer GHCR tokens |
| LUM-265 (pricing page + checkout) | Tiers incl. Teams; Pro = household | Lines per §3 (remote access sub, connectors sub, KG one-off, goodwill); **drop Teams** |
| LUM-266 (premium install docs) | Private-image pull | Store/entitlement install + relay setup |
| LUM-267 (Core upgrade prompt) | `licensed:false` for KG | Store/upgrade prompts for paid add-ins; no "KG paywall after free" framing |
| LUM-271 (Stripe product + price) | EUR 99/yr (then 79) | Re-price to the §3 lines; remote-access subscription is the headline |
| LUM-96 (licence validation) | Validate household Pro | Offline Ed25519 entitlement verifier (per §9) |
| LUM-169 (managed-keys) | Evaluate | Separate optional add-on, deferred |
| **New** | — | **Relay service**, **official mobile apps**, **plugin registry + permissions/sandbox/signing**, **entitlement rail** |

## Consequences

**Easier / better:**
- Revenue rests on things people *actually* pay for (remote access, connector upkeep) with instant, recurring, felt value — not on the highest-churn feature.
- Most of the shipped open-core split (strip-list, `GRAPH_MODE`, `CapabilityLicenseMode`) holds → low rework.
- The open ecosystem compounds the trust moat instead of giving away the business.
- Honest pricing (recurring only where there's recurring cost) is defensible and self-explaining.
- Multi-user is free → the single-user/family story is coherent, and the account-creation gate disappears.

**Harder / foreclosed:**
- **The moat is soft.** It must be continuously earned through execution and trust; a better third-party app/relay can take share if the official offering slips. There is no legal/code lock.
- **Monetisation now depends on serving Persona C.** If Lumogis stays a Docker/self-build tool for Persona A, there is effectively no business — only a (legitimate) goodwill-funded project. This is the central strategic bet (see Revisit conditions).
- **New operational burden:** running a relay (uptime, bandwidth, abuse) and maintaining official connectors and an app-store presence — real, ongoing, and outside the current Docker-only footprint.
- **Plugin security is now on the critical path.** Opening the ecosystem without a permission/sandbox/signing model would expose users' entire private datasets.

## Open decisions

- **Locked (operator, 2026-06-15):** trust/ecosystem moat (not code); base + multi-user free; relay = the one operated cloud, no data hosting; remote access = primary subscription; official connectors = paid subscription; KG = optional one-off paid add-in, off by default; open ecosystem with official registry; goodwill licence for Persona A.
- **Still open (implementation):** relay build-vs-buy (own vs WireGuard/Tailscale-derived vs commercial tunnel); exact price points; entitlement build-vs-buy (in-house Ed25519 vs keygen.sh/Paycheck); which connectors are "official paid" vs "community free" at launch; grace-window length and per-line lapse behaviour; plugin permission-model design; whether the KG add-in is sold standalone or inside a "power pack".

## Revisit conditions

- **Persona-C commitment falters** (product stays terminal/Docker-only) → there is no paying base; fall back to a goodwill/donation-funded project and **stop building the entitlement/relay machinery** (it would be a toll booth on a road only Persona A drives).
- **Remote-access willingness-to-pay is unproven** → validate with a beta before building the full relay + LUM-260 chain; do not build the chain ahead of the signal.
- **A third party captures the relay/app layer** → compete on trust/official/integration, not price; revisit registry/signing leverage.
- **Managed-keys (LUM-169) demand proves out** → evaluate as a separate add-on.
- **Legal review** of relay data-handling, AGPL boundaries for proprietary plugins/relay, and the goodwill-licence/EULA → amend as required.

## Status history

- 2026-06-15: Accepted via `/explore LUM-442` dialogue + operator decision. Reverses the prior LUM-442 lean (Option A KG-as-pillar, Pro = household): KG demoted to optional one-off paid add-in (off by default); multi-user made free; primary lever moved to relay-based remote access; moat relocated from a KG code-gate to trust + official apps/relay + an open plugin registry; managed-keys deferred. LUM-260 chain re-scoped onto a generic entitlement rail. Exploration rewritten in parallel (devtools).
