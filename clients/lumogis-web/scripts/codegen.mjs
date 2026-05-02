#!/usr/bin/env node
/* SPDX-License-Identifier: AGPL-3.0-only */
//
// codegen.mjs — runs openapi-typescript against the committed snapshot at
// ../../openapi.snapshot.json (relative to this file) or against the live
// orchestrator at $LUMOGIS_OPENAPI_URL when --live is passed. Generated
// types land in src/api/generated/openapi.d.ts (gitignored).
//
// Modes:
//   pnpm codegen              -> regenerate types from snapshot
//   pnpm codegen --live       -> regenerate types from $LUMOGIS_OPENAPI_URL
//   pnpm codegen --check      -> compare snapshot to $LUMOGIS_OPENAPI_URL,
//                                 exit 1 if they drift. Used in CI per parent
//                                 plan §"Phase 1 Pass 1.1 item 1" — the SPA
//                                 snapshot must match the shipped spec.

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");
const snapshotPath = resolve(repoRoot, "openapi.snapshot.json");
const outDir = resolve(repoRoot, "src/api/generated");
const outFile = resolve(outDir, "openapi.d.ts");

const args = new Set(process.argv.slice(2));
const useLive = args.has("--live");
const checkMode = args.has("--check");
const liveUrl = process.env.LUMOGIS_OPENAPI_URL || "http://localhost:8000/openapi.json";

if (!existsSync(snapshotPath)) {
  console.error(`codegen: snapshot missing at ${snapshotPath}`);
  console.error(
    "Generate it from repo root: cd orchestrator && python -m scripts.dump_openapi --pretty --sort-keys --out ../clients/lumogis-web/openapi.snapshot.json",
  );
  process.exit(2);
}

if (checkMode) {
  await runCheck();
  process.exit(0);
}

const source = useLive ? liveUrl : snapshotPath;
await mkdir(outDir, { recursive: true });

const result = spawnSync("npx", ["--yes", "openapi-typescript", source, "-o", outFile], {
  stdio: "inherit",
  cwd: repoRoot,
});

process.exit(result.status ?? 1);

// ----------------------------------------------------------------------------

async function runCheck() {
  const snapshot = readFileSync(snapshotPath, "utf-8");
  let live;
  try {
    const res = await fetch(liveUrl);
    if (!res.ok) {
      console.error(`codegen --check: live spec fetch failed: HTTP ${res.status}`);
      process.exit(2);
    }
    live = await res.text();
  } catch (err) {
    console.error(`codegen --check: live spec fetch error: ${err.message ?? err}`);
    console.error(`Set $LUMOGIS_OPENAPI_URL or start the orchestrator first.`);
    process.exit(2);
  }

  // Normalise whitespace + key order for a stable diff. Both files come from
  // FastAPI's deterministic OpenAPI generator so canonical re-serialisation
  // gives a byte-for-byte comparison.
  const a = canonicalise(snapshot);
  const b = canonicalise(live);
  if (a === b) {
    console.log("codegen --check: snapshot matches live spec ✓");
    return;
  }

  console.error("codegen --check: snapshot drifts from live spec");
  console.error("Refresh by running:");
  console.error(
    "  cd orchestrator && python -m scripts.dump_openapi --pretty --sort-keys --out ../clients/lumogis-web/openapi.snapshot.json",
  );
  console.error("Then commit the change.");
  process.exit(1);
}

function canonicalise(text) {
  const obj = JSON.parse(text);
  return JSON.stringify(sortObject(obj));
}

function sortObject(value) {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value !== null && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      out[key] = sortObject(value[key]);
    }
    return out;
  }
  return value;
}
