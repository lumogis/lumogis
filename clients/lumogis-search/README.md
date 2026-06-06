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

Maintainers publish unsigned installers for **`lumogis/lumogis`** by pushing a **`search-v*`** tag (for example `search-v0.7.0`) after the public AGPL export; the tag must match **`version`** in `src-tauri/tauri.conf.json`. GitHub Actions workflow **`.github/workflows/search-overlay-build.yml`** builds a four-platform matrix and attaches bundles to the Release. If a matrix leg fails after a Release was created, delete the Release, fix the failure, and re-tag.

## License

Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis. Licensed under [AGPL-3.0-only](https://www.gnu.org/licenses/agpl-3.0.html).
