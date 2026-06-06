# Lumogis Search

AGPL-3.0-only desktop overlay for searching memory on a **household Lumogis server**.

Lumogis Search connects to your self-hosted orchestrator over HTTP. It does not ship or start local databases, vector stores, or LLM runtimes. Sessions are stored in the OS keychain when the server has auth enabled.

## Requirements

- A running Lumogis stack (orchestrator reachable from this machine)
- Node.js 20+ and Rust 1.74+ for local builds

## Build

```bash
npm ci
npm run build
cd src-tauri && cargo build
```

Release installers:

```bash
npm run tauri:build
```

From the repository root, **`make search-build`** runs the same client-only Tauri build locally.

Maintainers publish unsigned installers for **`lumogis/lumogis`** by pushing a **`search-v*`** tag (for example `search-v0.1.0`) after the public AGPL export; the tag must match **`version`** in `src-tauri/tauri.conf.json`. GitHub Actions workflow **`.github/workflows/search-overlay-build.yml`** builds a four-platform matrix and attaches bundles to the Release. If a matrix leg fails after a Release was created, delete the Release, fix the failure, and re-tag.

## Personas

**Persona A and Persona B ship the same binary** (`lumogis-overlay-{macos-arm64,macos-x64,linux-x64,windows-x64}` per [ADR 082](../../docs/decisions/082-lum-433-search-overlay-public-ci.md) and `.github/workflows/search-overlay-build.yml`). Only the **server URL** you enter in first-run onboarding differs. Installers are **unsigned** in v1 — acceptable for household use; code signing is tracked as **LUM-406**.

### Persona A — Docker-track (localhost)

For operators who run Lumogis **Core via Docker Compose** on the same machine as Search.

1. **Start Core:** `docker compose up -d` from the repo root (see [`docs/deployment/quickstart.md`](../../docs/deployment/quickstart.md)).
2. **Verify orchestrator:** open **`http://localhost/healthz`** (Caddy on port 80 — default quickstart path). Use **`http://localhost:8000/healthz`** only when bypassing Caddy.
3. **Download installer** from **[lumogis/lumogis Releases](https://github.com/lumogis/lumogis/releases)** — pick the `search-v*` tag on the **public** repo. Artefact names: `lumogis-overlay-macos-arm64`, `lumogis-overlay-macos-x64`, `lumogis-overlay-linux-x64`, `lumogis-overlay-windows-x64` (built by `.github/workflows/search-overlay-build.yml`; see [ADR 082](../../docs/decisions/082-lum-433-search-overlay-public-ci.md)).
4. **First-run onboarding:** set server URL to **`http://localhost`** (or your operator-published origin on this host). Search calls **`GET /healthz`**, then sign in when **`AUTH_ENABLED=true`**.
5. **Optional — other devices:** you can use Search locally while household members use Lumogis Web over [remote access](../../docs/deployment/remote-access.md) (Tailscale, etc.).

### Persona B — household member

Same installer as Persona A. Your operator provides the **household server URL** (LAN or remote-access HTTPS). Download the release from **[lumogis/lumogis Releases](https://github.com/lumogis/lumogis/releases)** or use a copy from your operator, then enter their URL in first-run onboarding. See the [Persona A / B / C distribution matrix](../../docs/LUMOGIS_REFERENCE_MANUAL.md#persona-a--b--c--distribution-matrix).

## License

Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis. Licensed under [AGPL-3.0-only](https://www.gnu.org/licenses/agpl-3.0.html).
