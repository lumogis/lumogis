# ADR-229: The knowledge graph is AGPL-3.0-only and ships in the public export — restoring ADR-011's posture
**Status:** Accepted
**Created:** 2026-11-07
**Last updated:** 2026-11-07
**Decided by:** operator decision (2026-09-15); plan hardened through six review rounds (self-review, two blind critiques fable-5/sonnet-5, arbitration, then rounds 3–5)
**Plan:** `.cursor/plans/LUM-855-kg-agpl-relicence.plan.md`
**Reviews:** `.cursor/reviews/kg-agpl-relicence/`
**Linear:** [LUM-855](https://linear.app/lumogis/issue/LUM-855/relicence-the-knowledge-graph-to-agpl-30-only-and-publish-it)
**Supersedes:** [ADR 042](042-kg-public-private-export-boundary.md) (KG public/private export boundary) — in full
**Amends:** [ADR 101](101-lum-442-commercialisation-ecosystem-model.md) §3/§4/§9 and its code posture · [ADR 206](206-lum-809-email-intelligence-commercial-line.md) (the gate's meaning) · [ADR 136](136-lum-263-entitlement-issuer-jwt-schema.md) (claim enum) · [ADR 134](134-lum-301-codegraph-code-structure-ingest.md) (scope header) · [ADR 150](150-lum-558-graphiti-build-vs-wrap.md) (a retired rationale) · [ADR 002](002-graph-store-falkordb.md) (premium framing) · ADR 011 (four contradicted passages)
**Relates:** ADR 011 (graph service extraction — the posture this restores), ADR 015 (scope union), ADR 035 (bearer fail-closed), LUM-856 (self-hosted runner export, found during this work)

## Context

The knowledge graph has been absent from the public AGPL export since 2026-05-15, when
ADR-042 locked a strip boundary around it. Three facts, none of which were in the original
framing, decide what this change actually is.

**The KG was never proprietary by licence.** Of 107 files across the KG surface, 95 carry
`SPDX-License-Identifier: AGPL-3.0-only`; the other 12 are fixtures, HTML, Markdown and
compose overlays that carry no header, per repo convention for non-source files. Nothing
carries a non-AGPL identifier, a `-or-later` form, a third-party copyright block, or
vendored third-party code. The graph was proprietary by **non-distribution**, not by licence.

**It was already published once.** `services/lumogis-graph/` shipped in the public
`lumogis/lumogis` repository from commit `2440fda` (**2026-05-03**) to `77a41e7`
(**2026-05-15**) — 55 files, 12,784 lines, AGPL-headered — and was removed by the 0.3.1
snapshot cut the same day ADR-042 was created. ADR-011's **2026-04-30 status-history note** still carries the
contemporaneous record (cited by date, not line number, because line numbers shift): *"source ships in the public `lumogis/lumogis` repo; commercial
packaging may still omit or replace the image."* This ADR therefore **restores ADR-011's
posture and supersedes a 12-day retraction**; it is not a first disclosure.

**There is no paywall to dismantle.** There is zero entitlement enforcement anywhere in the
product: no JWS verification, no `402` path, no `entitlements[]` check. LUM-262, which
ADR-101 §9 named as the runtime gate, was never built, and ADR-206 already described the
boundary as "a capability check, not a licence check" in the interim. The only gates that
ever existed were the strip list and a config switch.

Against that, ADR-101's own reasoning for charging for the graph does not survive. §3 sets
the pricing principle — *charge recurring only where Lumogis carries a recurring cost* — and
§Context records that the KG "fails all three" of used-now / missed-when-gone / instant-value,
citing ~68% six-month abandonment for PKM/graph tools. A capability that runs entirely on the
user's hardware, that the project itself rates as its highest-churn feature, is a poor thing
to sell and a good thing to give away.

## Decision

**The knowledge graph is AGPL-3.0-only and ships in the public export tree.** It is retired
as a commercial SKU.

1. **Strip boundary.** The 17 KG entries leave `scripts/public-export-strip-list.txt`:
   `services/lumogis-graph/` (including `codegraph`), `orchestrator/plugins/graph/`,
   `orchestrator/adapters/falkordb_store.py`,
   `orchestrator/services/graph_webhook_dispatcher.py`,
   `orchestrator/services/kg_premium_core.py`, `orchestrator/services/edge_quality.py`,
   `orchestrator/tests/premium/`, the five graph compose overlays, and the five stripped KG
   ADRs (007, 008, 009, 011, 035). The other 20 entries — official paid connectors, maintainer docs and build artefacts —
   **stay**: commercial components stay outside this licence. This
   shrinks the strip list; it does not retire the export machinery.

2. **No behaviour change.** `GRAPH_MODE` keeps its three tokens and its `disabled` default.
   Nothing a user runs today changes. The fail-soft wiring in
   `orchestrator/main.py::_wire_graph_mode_handlers` is unchanged: it was written to degrade
   when premium modules were absent and is equally correct as "degrade when the optional
   graph component is not deployed". Its four `reason` values
   (`inprocess_plugin_absent`, `service_import_error`, `invalid_graph_mode_env`,
   `edge_quality_import_error`) are asserted in tests and must not be reworded.

3. **The capability tag.** The graph capability reports
   `CapabilityLicenseMode.COMMUNITY` at both sites in
   `services/lumogis-graph/routes/capabilities.py` (per-tool `:88`, whole-manifest `:117`),
   and the manifest is renamed from `"Lumogis Graph Pro"` to `"Lumogis Graph"` at all three
   sites it appears: `routes/capabilities.py:113`, `main.py:215` (the FastAPI title, served
   on `/docs` and `/openapi.json`) and `kg_mcp/server.py:248` (the MCP description shown to
   connected clients). `CapabilityLicenseMode.COMMERCIAL` **remains in the enum** — official
   paid connectors still need it.

4. **`ADR-206`'s gate changes meaning, not mechanism.** Its rule was *"an email behaviour is
   premium if and only if it reads the knowledge graph."* The referent is gone, so the rule
   is replaced: **all email intelligence is core and AGPL.** The existing
   `GRAPH_MODE != "disabled"` check at each call site stays exactly as it is, reinterpreted
   as a **capability** check — is the graph deployed? — rather than a commercial one. The
   silent-degradation contract (graph absent → fall back to the core path, never error,
   never a blank surface) is unchanged and permanent. **No code change in the email stack.**

5. **Entitlements.** `ISSUABLE_ENTITLEMENTS` becomes `[]` in
   `lumogis-cloud/services/entitlement-issuer/src/claims.ts`. `GRAPH_ENTITLEMENTS` and
   `ALL_KNOWN_ENTITLEMENTS` are **kept**, so already-issued JWTs and stored D1 rows stay
   *known* and keep validating. `plan` stays pinned to `one_off`, so ADR-136's freeze holds
   and `src/jwt.ts`, `src/routes/license.ts` and `src/routes/stripe-webhook.ts` are not
   touched. Consequence to be explicit about: `parseEntitlements` checks `ISSUABLE_SET` at
   `claims.ts:41`, so **every issuance request is now rejected** — intended, and it means the
   issuer cannot mint any licence until a real SKU exists.

6. **The commercial model that survives** is ADR-101's honest remainder: remote access via
   the relay (recurring cost), official maintained connectors (recurring upkeep), managed
   keys/inference (LUM-169, recurring cost), and the optional goodwill licence. All three
   paid lines are untouched by this ADR. The generic entitlement rail stays in scope for
   them; it simply has no graph SKU on it.

7. **`orchestrator/plugins/graph/*` are `sys.path` shims over the service tree, not forks.**
   Recorded so a future reader does not count them among the four genuinely forked quality
   modules (`edge_quality`, `deduplication`, `entity_constraints`, `entity_quality`), whose
   convergence is tracked separately and is **not** a precondition of publication.

8. **Secure-by-default overlays.** `KG_ALLOW_INSECURE_WEBHOOKS` defaults to `false` in both
   `docker-compose.premium.yml:55` and `docker-compose.public-rc-stack.yml:52-53,:136-137`,
   matching `services/lumogis-graph/config.py:607`. The RC stack takes a fixed test secret
   from `config/test.env.example` and exercises the **authenticated** webhook path, matching
   production. A published reference deployment must not default to accepting unauthenticated
   webhooks.

9. **`GRAPH_MODE=inprocess` does not work on the standard image, and the docs must say so.**
   `orchestrator/Dockerfile:30` is `COPY orchestrator/ .`, so a Core image never contains
   `services/lumogis-graph/`; the `sys.path` wiring in `orchestrator/plugins/graph/__init__.py:66`
   finds nothing and the import fails, degrading to `disabled`. This is invisible today
   because the KG is stripped. **Publication makes it user-facing for the first time**, so
   the documentation must state that `service` is the only working mode on the standard image
   and that `inprocess` needs a build including the graph tree. The token is not removed —
   operators have it in `.env` files.

## Provenance and dating — read this before writing release copy

The original motivation was to publish before an employment contract with an IP-assignment
clause began. **That contract has since started**, which changes what this publication can
and cannot do, and the record must be accurate about it:

- **The pre-contract evidence is the May 2026 public window** (`2440fda` → `77a41e7`) and
  **ADR-011's 2026-04-30 status-history note**, written for an unrelated reason. Both predate employment
  by months and are held on third-party infrastructure rather than asserted by us.
- **This snapshot is a publication performed during employment.** It cannot establish
  pre-contract provenance; it makes already-pre-existing work visible.
- Therefore **the CHANGELOG entry and release notes must date the work separately from its
  publication** — when the graph was authored and first published (May 2026) versus when this
  snapshot was cut. Nothing may read as though the knowledge graph came into being now.
- **ADR-011's 2026-04-30 status-history note must be left intact** when ADR-011 is amended. It is evidence. (ADR-229's own amendment added a header note to ADR-011, which shifted that line from 11 to 17 — hence citing it by date.)
- Publication timing never determined ownership. A prior-inventions / excluded-IP schedule
  naming Lumogis and its repositories is the mechanism for that, and is out of scope here.

## Alternatives considered

- **Keep the KG proprietary, as ADR-042 and ADR-101 §4 had it.** Rejected. ADR-101's own
  §3 pricing principle forbids charging recurring for pure-local compute, and it had already
  demoted the KG from pillar to "gravy, not a pillar". Selling the project's
  highest-churn, lowest-felt-absence feature was the weakest of its revenue lines, and the
  `coding_graph` SKU in particular targeted Persona A — the persona ADR-101 §8 says "will pay
  for almost nothing".
- **Relicense the other commercial components too.** Out of scope and rejected for now:
  they are different assets with a different argument, and widening this
  change would have delayed it.
- **Converge the four forked quality modules first.** Considered and deliberately deferred.
  The original justification — that publishing forked modules traps the first outside
  contributor — does not hold while there are no contributors, and holding publication for a
  tidiness refactor was the wrong trade.
- **Strip-list `ci.yml` to stop the self-hosted runner leaking.** Rejected: it would also
  remove the file-presence guards that give the public tree its graph CI coverage, and leave
  the public repo with no CI at all. Solved instead by rewriting runner labels on export and
  gating the result fail-closed (LUM-856).

## Consequences

**Easier / better**
- The public claim is true for the first time. `lumogis-site/public/index.html:89` says
  "Open source · AGPL" and `privacy/index.html:90` invites readers to "read exactly what the
  software does on GitHub" — neither held while the component that reads everything was
  withheld. For a product whose pitch is local-first privacy, the graph was the worst possible
  thing to keep closed.
- The vision becomes demonstrable. `lumogis-vision-v5.md:25` — "over time it builds a
  knowledge graph of your life" — was unverifiable in the public tree.
- ~26k lines move from shipped-but-unverifiable-in-public-CI to covered: `ci.yml` already
  guards every graph step on file presence, so unit tests and the second Bandit pass activate
  automatically.
- The public coverage matrix stops lying: `docs/testing/TEST-COVERAGE-MATRIX-core.md` rows
  1.5.12 and 1.7.17 cite `orchestrator/tests/premium/...` paths that do not exist in the
  public tree today.
- The open-plugin-ecosystem bet (ADR-101 §6, ADR-170) gains its most interesting substrate:
  third parties can build against a real, inspectable graph.

**Harder / owned**
- Publication is irreversible. Anything published cannot be unpublished.
- The entitlement issuer can mint nothing until a real SKU exists (§5).
- Four forked quality modules become publicly visible, with their convergence tracked but not
  done. One carries a known latent defect: the graph copy of `deduplication.py` dropped the
  `scope: 'personal'` clause from a Qdrant filter that Core still has, inert only
  incidentally, and asserted by no test on either side.
- Public CI wall-clock rises as graph suites activate.
- `GRAPH_MODE=inprocess` becomes a visible rough edge (§9).

## Revisit conditions

- **A credible paid graph offering appears** — a hosted graph, or graph compute Lumogis pays
  for — then §5 and ADR-101 §3 are reopened. Local compute remains unsellable under §3.
- **Outside contributors arrive**, which makes the four forked modules a real cost rather
  than untidiness, and raises the priority of their convergence.
- **The CDN loads in `services/lumogis-graph/static/graph_mgm.html`** (cytoscape from cdnjs
  `:10`, alpine from jsdelivr `:25`) need a disposition before anyone calls the published
  page privacy-preserving.
- **Legal review** of the publication's interaction with the employment contract, or of the
  prior-inventions schedule, may require amending the Provenance section.

## Status history

- 2026-05-03: `services/lumogis-graph/` published in `lumogis/lumogis` (`2440fda`).
- 2026-05-15: removed by the 0.3.1 snapshot (`77a41e7`); ADR-042 created the same day.
- 2026-09-15: operator decision to relicense; plan written and reviewed.
- 2026-09-21: export gates for LUM-856 hardened through rounds 3–5; plan corrected.
- 2026-11-05: the export gates merged to `dev` (`b492394`).
- 2026-11-07: this ADR written. Accepted; supersedes ADR-042.
- 2026-11-10: public-boundary wording (LUM-898).
