# ADR 081: Export boundary reconciliation — Lumogis Search vs bundled appliance (LUM-434)

**Status:** Finalised
**Created:** 2026-06-05
**Last updated:** 2026-06-05
**Decided by:** LUM-434 (docs reconciliation after **LUM-432** extraction and **LUM-435** Hub de-dup)
**Finalised by:** LUM-434 implementation on **`dev`**

## Context

Programme **LUM-430** required a clear split between the **AGPL household search overlay** and the **bundled Persona C appliance** (local Core + supervisor). **LUM-329** chose Tauri 2 overlay behaviour; **LUM-398** added client-only distribution; **LUM-396** added bundled sidecars. An interim single-tree implementation drifted from the settled **LUM-329** intent (public **Lumogis Search**). **LUM-432** extracted **`clients/lumogis-search/`**; **LUM-435** relocated and de-duplicated the bundled appliance onto Search. **ADR 080** records the export-split retrospective.

**LUM-434** reconciles the durable record: canonical product names, paths in the maintainer tree, and public-export self-containment.

## Decision

### Canonical boundary (as of 2026-06-05)

| Surface | Product | Public AGPL export |
| --- | --- | --- |
| Client-only memory-search overlay (Personas A/B) | **Lumogis Search** (`clients/lumogis-search/`) | **Included** |
| Bundled Persona C appliance (Core + supervisor + built-in Search UI) | **Lumogis Hub** | **Excluded** |
| Future full native client | **Lumogis Desktop** | N/A (future) |

Maintainer-only path detail (private docs): bundled appliance tree and CI are outside the public export; see **`docs/private/lumogis-taxonomy-final.md`** and **`AGENTS.md`** maintainer boundary — not repeated here.

### Public-export doc rule

Public-shipping ADRs and READMEs describe **Lumogis Search** on its own terms. They do **not** name maintainer-only trees, strip-list mechanics, or “removed/private desktop” framing. Maintainer-only detail lives in private docs (`docs/private/`, **`AGENTS.md`** maintainer boundary, **`docs/LUMOGIS_CONTEXT_PACK.md`**).

### LUM-398 / LUM-329 relationship

- **LUM-329** — authority for **overlay behaviour** (hotkey, search contract, keychain, open/reveal).
- **LUM-398** — authority for **Persona B client-only distribution UX** (onboarding, role-gated settings); artefact is **Lumogis Search**, not an interim private tree.
- The interim colocation under a single proprietary tree was **implementation drift**, corrected by **LUM-432** / **LUM-435** / this ADR.

## Supersedes (path, export, and product naming only)

The following ADRs remain useful as **behaviour** and **as-shipped** records; their **path**, **export**, and **single-tree product** framing is superseded by this ADR and **ADR 080**:

- **[ADR 069](069-lum-329-tauri-search-overlay.md)** — overlay behaviour → implement at **`clients/lumogis-search/`**
- **[ADR 072](072-lum-398-client-only-overlay.md)** — Persona B distribution → **`clients/lumogis-search/`**
- **[ADR 076](076-lum-396-bundled-sidecar-process-manager.md)** — bundled supervisor architecture (maintainer-only; excluded from public export per **ADR 042**)

## Consequences

- **Easier:** One canonical boundary for assistants, export checks, and programme **LUM-430** children.
- **Harder:** Historical ADR bodies retain past-tense implementation notes; readers must check **Status** / **Supersedes** headers.

## Status history

- 2026-06-05: Finalised by LUM-434 reconciliation on **`dev`**.

## Relation to other decisions

- **[ADR 080](080-lum-430-lumogis-search-public-export.md)** — as-shipped export split evidence (**LUM-432**)
- **[ADR 042](042-kg-public-private-export-boundary.md)** — strip-list export mechanism
- **`docs/private/lumogis-taxonomy-final.md`** — canonical product names (private)

## Linear linkage

- **LUM-434** — https://linear.app/lumogis/issue/LUM-434/docs-reconcile-desktop-export-boundary-adrs-and-fix-lum-398-cross
- **Parent:** **LUM-430**
