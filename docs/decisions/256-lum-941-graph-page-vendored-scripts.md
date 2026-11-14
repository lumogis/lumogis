# ADR-256: Graph pages load their scripts only from same-origin vendored files (LUM-941)

**Status:** Finalised
**Created:** 2026-11-11
**Last updated:** 2026-11-11
**Decided by:** claude-opus-5-5 (/explore --headless LUM-941)
**Finalised by:** /verify-plan 2026-11-11 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-941-graph-page-vendored-scripts.plan.md`
**Exploration:** `.cursor/explorations/LUM-941-graph-page-vendored-scripts.md`
**Draft mirror:** `.cursor/adrs/LUM-941-graph-page-vendored-scripts.md`
**Relates:** [ADR 229](229-lum-855-kg-agpl-relicence.md) (KG relicence; this ADR resolves the CDN loads it left open at `:195`)

## Context

The Knowledge Graph management page (`graph_mgm.html`) ships in two byte-identical copies, one in Core (`orchestrator/static/`) and one in the graph service (`services/lumogis-graph/static/`). Both loaded cytoscape 3.28.1 from cdnjs and Alpine 3.14.8 from jsDelivr. The cytoscape tag's `integrity` value was not a valid sha512 digest (86 characters, 63 bytes decoded), and the Alpine tag had none. `orchestrator/static/graph_viz.html` loaded the same cytoscape the same way. Their `onerror` fallbacks requested `/static/*.js`, which neither service serves.

ADR 229 made the Knowledge Graph AGPL and public. The first public release that includes it is 0.12.0 (LUM-855 Pass 7). A local-first, privacy-first product must not ship an admin page that tells two third-party CDNs about the household each time it opens, or one that loads an unpinned script.

The exploration also found that Caddy's site-wide CSP (`script-src 'self'`, `docker/caddy/Caddyfile:69`) blocks every script on these pages, inline scripts included, and that `require_admin` returns 401 to a browser navigation when sign-in is on. So the page runs only on direct loopback or graph-service access with sign-in off. Those defects are outside this decision and are recorded as a follow-up bug.

## Decision

Vendor cytoscape 3.28.1 and Alpine 3.14.8 (both MIT), unchanged, into `static/vendor/` in each service, with a `LICENSES.md` that keeps both notices. Serve them from an allowlisted `GET /graph/assets/{name}` on each service, behind the same auth gate as the page, with no new bypass prefix:

- **Core:** `orchestrator/routes/admin.py` `graph_asset`, `Depends(require_admin)`.
- **Graph service:** `services/lumogis-graph/routes/mgm.py` `get_graph_asset`; `auth._requires_admin` covers `/graph/assets/`.

The allowlist check runs before any path is built. Point every `<script src>` on the graph pages at that same-origin path with a `sha384` `integrity`, and delete the dead fallback loaders.

Tests pin the vendored files' SHA-256 so the copies can't drift. They also fail if any HTML under `orchestrator/static`, `orchestrator/dashboard` or `services/lumogis-graph/static` references a third-party origin in its markup or CSS (`orchestrator/tests/test_graph_page_assets.py`). `.gitattributes` marks both vendor folders `-text -diff linguist-vendored`, so no checkout converts their line endings and breaks the SRI hashes. The route sends `Cache-Control: max-age=31536000, immutable` **without** `public`, so a shared cache never stores a response to a Bearer-authenticated request. Both routes are `include_in_schema=False`, so the Web client's OpenAPI snapshot does not change. The Caddy CSP is not loosened.

## Alternatives Considered

- **Document the egress and add SRI to Alpine:** rejected. The CDN calls remain, which breaks local-first, and the page still doesn't work offline.
- **Vendor and make the page CSP-clean (external `graph_mgm.js`, the `@alpinejs/csp` build, a meta tag for the Core base URL):** deferred. It is the right end state if the legacy page stays. But it means rewriting about 36 Alpine expressions on an untested page, and it still leaves the auth 401 and Core's missing graph routes unfixed. It is recorded as the follow-up bug.
- **Vendor and relax Caddy's CSP for the graph pages:** rejected. It would add `'unsafe-eval'` to an admin page.
- **Retire the page in favour of a Lumogis Web graph view (LUM-196 / LUM-161):** out of scope for a release gate.
- **Vendor only into the graph service and retire or redirect Core's `/graph/mgm`:** rejected for this chunk. Core's `/graph/mgm` is a registered admin route and is documented; retiring it is a product decision for the follow-up bug.
- **A `StaticFiles` mount at `/graph/assets`:** rejected. With an allowlisted handler, a file that later lands in `static/vendor/` (such as `LICENSES.md`) is never served by accident.
- **A shared vendor path both images COPY from:** rejected. It would couple the standalone graph image to `orchestrator/`; the cross-tree hash test gives the same guarantee.

## Consequences

- **Easier:** Opening the graph pages makes no third-party request, and they work offline wherever they run today (proved at verify time in headless Chromium with all off-origin requests blocked). No page Core or the graph service serves has a CDN dependency in 0.12.0. The maintainer tool `tools/linear-graph.html` still loads d3 from jsDelivr, now pinned to `d3@7.9.2/dist/d3.min.js` with SRI. A later CSP-clean rewrite can build on the same-origin files without redoing them.
- **Harder:** Bumping cytoscape or Alpine is now a manual edit, done in one commit, to both vendored copies, their SHA-256 pins, both allowlists and the `integrity` attributes. A bump always uses a new file name: the one-year immutable cache means new bytes under an old name never reach a browser that has the old file. The two route handlers are duplicated by design. Tests pin their allowlists and cache headers to each other, but the graph service's own test lane can't see Core's tree, so both lanes must run.
- **Future chunks must know:**
  - No page Core or the graph service serves may load a script, stylesheet, image or font from a third-party origin. The guard test enforces this for markup and CSS; it does not see URLs built at runtime by JavaScript.
  - The two page copies must stay byte-identical until the pages are converged or retired.
  - `x-cloak` may only sit on an `x-data` root or inside one. Alpine never clears it elsewhere. The page had `<body x-cloak>` from its first commit, so it stayed blank even with working scripts. Verification moved it onto `#shell`, and `test_x_cloak_only_inside_alpine_roots` guards it.
  - Vendoring doesn't make the page work through Caddy or with sign-in on. That is the follow-up bug.

## Implementation notes (verify-plan 2026-11-11)

- Vendored bytes match the npm registry tarballs and the plan's pre-fixed values: cytoscape `92d752b4…d4c6` (365,827 bytes), Alpine `b600e363…5643` (44,758 bytes).
- Offline proof, headless Chromium, off-origin requests aborted: the baseline page (`4fb474c1`) gives `ok: false`, both CDN URLs aborted, neither library defined. The branch page through the graph-service router gives `ok: true`, both libraries defined, Alpine initialised on `#shell`, the shell visible, nothing aborted.
- Chromium rejected the old cytoscape `integrity` value outright ("Failed to find a valid digest"), so before this change the CDN load was blocked even online (observed at implement time).

## Revisit conditions

- The legacy graph pages are replaced by a Lumogis Web view (LUM-196 / LUM-161). Delete the vendored files, the routes and the guard's `services/lumogis-graph/static` root with the pages.
- The follow-up bug chooses the CSP-clean path. Swap the Alpine file for `@alpinejs/csp` of the same pinned version under this same vendoring rule.
- A CVE is published against cytoscape 3.28.x or Alpine 3.14.x. Bump the vendored versions.
- Lumogis adds a JS build step to a Python service image for another reason. Reconsider bundling instead of committing minified files.

## Status history

- 2026-11-11: Draft created by /explore
- 2026-11-11: Revised during /review-plan --arbitrate R1 (scope of the "no CDN" claim, `.gitattributes`, cache header without `public`, guard scope, ADR 229 link). Decision unchanged.
- 2026-11-11: Finalised by /verify-plan. The implementation confirmed the decision. Verification also moved `x-cloak` from `<body>` to `#shell`, a pre-existing defect the offline proof exposed; that is an addition, not a deviation from the decision.
