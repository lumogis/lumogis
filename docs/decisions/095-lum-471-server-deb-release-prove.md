# ADR-095: Lumogis Server deb release build — proved packaging + Linux lib roots

**Status:** Finalised
**Created:** 2026-06-11
**Last updated:** 2026-06-11
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-06-11 (Composer)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/lum-471-server-deb-release-prove_retro.md`
**Draft mirror:** `.cursor/adrs/lum-471-server-deb-release-prove.md`

> Private Hub/server packaging scope; stripped from public AGPL export like ADR-093/094.

## Context

[LUM-471](https://linear.app/lumogis/issue/LUM-471) was opened during `/verify-plan` **LUM-469** because Linux P0 server code and `cargo build --release --bin lumogis-server` passed, but the full Tauri deb chain (`make server-build` → **`Lumogis-Server_*.deb`**) had not been run. ADR-094 shipped the server profile; this ADR records the **release packaging prove** and fixes required for installed-deb path resolution.

## Decision

**Treat Lumogis Server as a separate deb product with generated sidecar metadata and dual Linux lib-root resolution.**

1. **Release prove:** `make server-prove-server-deb-build` runs **`make server-build`** and validates `Lumogis-Server_*.deb` (size logged, `dpkg-deb -I`, `Package` field `lumogis-server`).
2. **Deb metadata generation:** `prepare-hub-deb-sidecars.sh` writes both Hub (`/usr/lib/Lumogis/`) and Server (`/usr/lib/Lumogis-Server/`) `deb.files` maps from the host triple — no static arch-specific JSON in source control.
3. **Runtime resolution:** `paths.rs` resolves sidecar `.real` files and `core-venv/` under **both** `/usr/lib/Lumogis` and `/usr/lib/Lumogis-Server`, preferring `Lumogis-Server` when the running binary is `lumogis-server`.

**As-shipped prove (2026-06-11):** `Lumogis-Server_0.1.0_amd64.deb` — **2593 MB**; build green in ~10 minutes with warm staged `core-venv/`.

## Alternatives considered

- **Cargo-only prove (`make server-prove-server-profile`)** — sufficient for supervisor logic but does not catch deb sidecar / `productName` regressions; rejected as LUM-471 closure.
- **Share Hub deb with two binaries** — rejected in LUM-469 arbitration; separate `Lumogis-Server_*.deb` required.
- **Single `/usr/lib/Lumogis/` root for both products** — rejected; server deb metadata uses `Lumogis-Server` prefix.

## Consequences

**Easier:**
- Maintainer can gate server releases with one Make target.
- Server deb install path matches runtime resolver.

**Harder:**
- Large artefact (~2.6 GB) limits how often full prove runs in CI.
- Install-from-deb + tray smoke still manual (**LUM-472** and optional future e2e).

**Future chunks must know:**
- Editing `tauri.server.deb-sidecars.json` by hand is wrong — regenerate via `prepare-hub-deb-sidecars.sh`.
- Fused Hub deb path unchanged (`/usr/lib/Lumogis/`).

## Revisit conditions

- Tauri 2 deb resource layout for `core-venv/` changes → re-prove and adjust `core_venv_dir()` candidates.
- Additional Linux architectures → extend prove matrix; prepare script already triple-driven.
- Co-install Hub + Server on one host → dual-root order must stay correct.

## Linear linkage (Product OS)

- **Issue:** [LUM-471](https://linear.app/lumogis/issue/LUM-471) (child of **LUM-469**)
- **Sibling:** **LUM-472** (graphical-login manual prove) — still open
- **Closure:** `/linear-update apply-closure LUM-471 --done` after human review

## Testing retrospective

| Check | Result |
| --- | --- |
| `make server-prove-server-deb-build` | OK — 2593 MB deb |
| `cargo test --features bundled --lib` | 33 passed |
| Deb install smoke | Not automated |

## Status history

- 2026-06-11: Finalised by `/record-retro` (retrospective as-built record for LUM-471)
