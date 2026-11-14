# ADR-239: Knowledge-graph publication as built, and the corrected publication history (LUM-855)
**Status:** Finalised
**Created:** 2026-11-07
**Last updated:** 2026-11-07
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-07
**Plan:** `.cursor/plans/LUM-855-kg-agpl-relicence.plan.md` (the work was planned; this records what implementation found outside it)
**Exploration:** `.cursor/explorations/kg_publication_as_built_retro.md`
**Draft mirror:** `.cursor/adrs/kg_publication_as_built.md`
**Linear:** LUM-855
**Corrects:** [ADR 229](229-lum-855-kg-agpl-relicence.md) §Context and §Provenance (the first-publication commit and date)

## Context

[ADR 229](229-lum-855-kg-agpl-relicence.md) decided that the knowledge graph is AGPL-3.0-only and
ships in the public tree. Implementing it found facts that ADR 229 and its plan did not have.
The most important is that ADR 229 dates the graph's first public publication one day late.
Finalised ADRs are not edited, so this record carries the correction and the other as-built
decisions.

## Decision

**1. The publication history, as the public repository records it.** The first public
publication of the graph service was commit `787429b` on **2026-05-02**, not `2440fda` on
2026-05-03. Fifteen of the seventeen paths ADR 229 un-strips were public before the 0.3.1
snapshot (`77a41e7`, 2026-05-15) removed them:

| Path | First public commit |
|---|---|
| `docker-compose.falkordb.yml` | `9f6d61d` (2026-03-19) |
| ADRs 007, 008, 009 | `234f3d3` (2026-05-01) |
| `services/lumogis-graph/` (55 files), `orchestrator/adapters/falkordb_store.py`, `orchestrator/services/graph_webhook_dispatcher.py`, `orchestrator/services/edge_quality.py`, the `premium`, `parity-premium`, `parity` and `public-rc-stack` compose overlays, ADR 011 | `787429b` (2026-05-02) |
| `orchestrator/plugins/graph/` | `b655f92` (2026-05-03) |
| ADR 035 | `fe3e9c0` (2026-05-08) |

Only `orchestrator/services/kg_premium_core.py` and `orchestrator/tests/premium/` are published
for the first time. Anything that cites the first publication cites `787429b` / 2026-05-02.

**2. What that history proves.** The committer's machine sets commit dates; the repository does
not certify them. The May 2026 window is evidence because those snapshots were public and their
content predates later events. The 0.12.0 GitHub Release object carries a server-side timestamp,
so it is the release's dated record, not the tag or the commit.

**3. Nothing in a published file claims a commercial tier.**
- The capability manifest reports `license_mode: community` and the name "Lumogis Graph", as do
  the `/docs` title and the MCP description.
- `services/lumogis-graph/codegraph/__init__.py`, which described itself as proprietary under an
  AGPL-3.0-only header, now describes itself correctly.
- Comments that called the graph premium or strip-listed are reworded. No filename, import, log
  event or `reason` value changed; renames remain follow-up work.

**4. `GRAPH_MODE=service` is the documented working mode.** Every doc that lists the
`GRAPH_MODE` tokens says that `service` is the only mode that works on the standard Core image.
`inprocess` needs a Core build that includes `services/lumogis-graph/`, because the image copies
`orchestrator/` only. The token stays.

**5. The site pages are regenerated at release, not before.** The renderer reads
`docs/capabilities.md` from the tree it runs in, so rendering from the integration branch would
advertise unreleased features. Site regeneration belongs to the publish step, from the release
line.

**6. The entitlement issuer can mint nothing.** The issuer is outside this repository:
- No entitlement is issuable, and no Stripe price maps to one. A checkout for a former graph
  price is recorded as a failure and issues nothing.
- Licences issued earlier still verify and can be revoked; the issuer's tests pin both.
- The test of repeat-customer `instance_id` stability cannot be reached while nothing is
  issuable. It returns with the first real SKU.

**7. Private-path citations.** Two docstrings in `services/lumogis-graph/` (`projection.py`,
`visibility.py`) cite maintainer paths that public readers cannot open, as Core files already
do. They are dead links, not disclosures, and are left as they are.

## Alternatives considered

- **Edit ADR 229 in place.** Not chosen: finalised ADRs are not edited. ADR 229 stays as written
  and this ADR corrects it.
- **Regenerate the site from the integration branch now.** Not chosen: it would publish
  features that are not released.
- **Rewrite the issuer's validate and revoke tests to expect rejection.** Not chosen: that would
  drop coverage of licences issued earlier, which must keep working.

## Consequences

- The publication-history case is stronger than ADR 229 states: almost the whole surface was
  public by 2026-05-02.
- Release copy must take its dates from this ADR, not from ADR 229.
- The dated record of the 0.12.0 publication is a GitHub Release object, not only a tag.

## Revisit conditions

- A legal review changes which evidence is cited: revise this ADR's Decision 1–2 together with
  ADR 229's Provenance section.
- The first real SKU is made issuable: restore the repeat-customer test in the issuer.
- The `premium` filenames are renamed: drop the "historical filename" notes in the module and
  overlay headers.

## Linear linkage (Product OS)

- **Existing issue:** LUM-855 (stays open; the release pass is outstanding).
- **New issue needed:** no.
- **Follow-ups:** a P2 (two admin-token comparisons in the graph service are not constant-time) is
  recorded in LUM-855's "Later" section.

## Testing retrospective

- **Export:** the export tree built from the branch passes `scripts/check-public-export.sh`
  with all seventeen paths present. `test_kg_paths_disjoint_from_strip_list` and
  `test_export_tree_includes_knowledge_graph` pin both facts.
- **Graph service:** 198 passed, 6 skipped (`make test-kg`, with the service's own requirements
  installed).
- **Orchestrator:** 6024 passed. The one failure is environment-only, and the test passes from a
  checkout outside the worktree tree.
- **Export gates:** 23/23.
- **Issuer:** 59 tests and the typecheck pass. Making the retired entitlements issuable again
  turns three tests red.
- **Not yet run:** `make verify-public-rc-full`, which runs with the release.

## Status history

- 2026-11-07: Finalised by /record-retro (retrospective).
