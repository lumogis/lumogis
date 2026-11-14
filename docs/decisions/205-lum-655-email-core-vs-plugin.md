# ADR-205: Email is a core, opt-in integration — not a marketplace plugin

**Status:** Accepted
**Created:** 2026-07-21
**Last updated:** 2026-08-06 (finalised from draft — implementation had already confirmed the decision)
**Decided by:** `/explore LUM-655` (Opus 4.8), 2026-07-21
**Linear:** [LUM-655](https://linear.app/lumogis/issue/LUM-655/explore-email-as-core-capability-vs-marketplace-plugin-and-the-opt) (unblocked LUM-642)
**Relates:** [ADR 193](193-lum-742-email-surface-routing.md) (attachment activation nests inside email activation), LUM-642 (Email surface v1 — consumed this decision), LUM-171 (capability marketplace registry — email is explicitly out of its scope), LUM-506 / LUM-442 (where the commercial line actually sits)
**Exploration:** `.cursor/explorations/LUM-655-email-core-vs-plugin.md`

> **Finalisation note (2026-08-06).** This ADR sat as a draft under `.cursor/adrs/`
> for two weeks while the decision it records was implemented and shipped. The
> code in `clients/lumogis-web/src/features/email/activation.ts` names LUM-655 in
> its header comment and implements exactly the recommended model. Numbering it
> is bookkeeping catching up with reality, not a new decision — nothing below
> changed in substance from the draft. See **Status history**.

## Context

LUM-642 (Email surface v1) needed to know whether the Email nav destination should gate on a plugin/marketplace activation. Recon found that model is greenfield: email was core and always-on, nav was role-gated only, and there was no per-user "enabled features" registry, marketplace page, or entitlement gate.

The "marketplace plugin" framing conflates three separable axes — **core vs commercial**, **always-on vs opt-in**, and **does a marketplace exist to perform activation**. They are independent: a feature can be core *and* opt-in with no marketplace and no price. Separating them dissolves most of the apparent tension.

## Decision

Email is **core and AGPL**, modeled as an **opt-in integration** the user activates by **configuring a mailbox** — the Home Assistant *integration* model, not an add-on/marketplace *plugin*. The Email surface and nav entry appear **iff email is activated**. Email ingestion, triage, and reply are core; the monetization layer is **managed Remote (LUM-506)** and **advanced email intelligence** as app-scope plugins — never the base email capability.

A general plugin/marketplace/activation system is a separate, larger decision. **Email is out of scope for it** regardless of whether that system is ever built.

## Alternatives Considered

- **Core, always-on (status quo)** — ignores the opt-in UX ask; clutters the app for users who never connect email. Rejected.
- **Commercial/paid plugin (licence/entitlement gate)** — paywalls a basic capability; violates the open-core rule ("no artificial limits in the core") and the local-first ethos. Rejected.
- **Community marketplace plugin (HACS-style installable)** — category error: first-party connectivity is an *integration*, not an add-on; also blocks a table-stakes feature on greenfield store infrastructure. Rejected.

Full detail and precedents (HA integrations vs add-ons/Apps and HACS; the open-core boundary) are in the exploration.

## What shipped

The decision is implemented, not merely accepted:

- `clients/lumogis-web/src/features/email/activation.ts` — `isEmailActivated(sources)` returns true when any source has `source_type === "imap"`. Deliberately isolated in its own module so a future explicit `email_enabled` toggle or marketplace (LUM-171) can swap the signal source without touching the view or the shell.
- `clients/lumogis-web/src/App.tsx` — the Email nav entry is pushed only when `emailActive`.
- LUM-658 added a `localStorage` activation cache so the nav item renders on cold load instead of popping in one RTT later. It is **fail-closed**: a read error or absent key hides the item.
- ADR-193 nests attachment activation inside `isEmailActivated`, so the attachment surfaces inherit this decision rather than defining their own gate.

## Consequences

**Easier**
- LUM-642 nav gating resolved with **zero new backend** — it reuses a per-user signal that already existed.
- Basic email stays free and core, consistent with AGPL, local-first, and the household chief-of-staff positioning where email triage is table stakes rather than an upsell.
- Forward-compatible: an enable-registry or marketplace later flips the same "email enabled" state through one module.

**Harder / foreclosed**
- Email is committed to being free and core — not a paid feature, not a marketplace add-on. Reversing that means overturning this ADR explicitly.
- The opt-in signal is "has-a-mailbox"; an explicit activate/deactivate toggle is deferred. A user cannot currently hide the Email surface while keeping a mailbox connected.

**Future chunks must know**
- The commercial line for email sits at **advanced intelligence/automation** (app-scope) and **managed Remote** — not the base surface. Precisely *where* that line falls is deliberately **not settled here**; it is tracked separately (see Status history).
- If a general marketplace/plugin-activation system is built, email stays a core integration outside it.

## Revisit conditions

- If Lumogis builds a general per-user plugin/activation registry, revisit whether email's nav gate reads it instead of the mailbox-configured proxy. Signal source only — no positioning change.
- If the commercialisation model (LUM-442) decides basic email should be a paid tier, this ADR must be **explicitly overturned**; it reverses the open-core stance rather than refining it.
- If clutter or opt-in needs prove "has-a-mailbox" is too implicit, promote the explicit `email_enabled` toggle from deferred to built.

## Status history

- 2026-07-21: Draft created by `/explore LUM-655`.
- 2026-07-21 – 2026-08-04: Implemented via LUM-642 (nav gate), LUM-658 (cold-load cache), LUM-742 / ADR-193 (attachment activation inherits it). The draft was never promoted, so ADR-193 shipped carrying a forward-reference to a decision that had in fact already been made.
- 2026-08-06: Finalised and numbered. ADR-193's forward-reference corrected. The one question the exploration deferred — where exactly the commercial line falls for email intelligence — was split out to its own Linear issue rather than left to lapse with LUM-655.
