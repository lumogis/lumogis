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
//   pnpm codegen --check      -> compare snapshot to python -m scripts.dump_openapi
//                                 (no running server), exit 1 if they drift.

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
// openapi.snapshot.json lives under clients/lumogis-web; repo root is three levels up from scripts/.
const repoRoot = resolve(__dirname, "..");
const appRoot = resolve(__dirname, "..", "..", "..");
const orchestratorDir = resolve(appRoot, "orchestrator");
const venvPython = resolve(appRoot, ".venv", "bin", "python3");
const pythonExe = process.env.LUMOGIS_OPENAPI_PYTHON || (existsSync(venvPython) ? venvPython : "python3");
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

const result = spawnSync("npx", ["openapi-typescript", source, "-o", outFile], {
  stdio: "inherit",
  cwd: repoRoot,
});

process.exit(result.status ?? 1);

// ----------------------------------------------------------------------------

async function runCheck() {
  const snapshot = readFileSync(snapshotPath, "utf-8");
  const tmpLive = join(tmpdir(), `lumogis-openapi-live-${process.pid}.json`);
  const dump = spawnSync(
    pythonExe,
    ["-m", "scripts.dump_openapi", "--pretty", "--sort-keys", "--out", tmpLive],
    {
      cwd: orchestratorDir,
      stdio: ["inherit", "inherit", "inherit"],
    },
  );
  if (dump.error || dump.status !== 0) {
    console.error("codegen --check: dump_openapi failed (see errors above)");
    try {
      unlinkSync(tmpLive);
    } catch {
      /* ignore */
    }
    process.exit(2);
  }

  let liveRaw;
  try {
    liveRaw = readFileSync(tmpLive, "utf-8");
  } finally {
    try {
      unlinkSync(tmpLive);
    } catch {
      /* ignore */
    }
  }

  const a = canonicalise(snapshot);
  const b = canonicalise(liveRaw);
  if (a === b) {
    console.log("codegen --check: snapshot matches orchestrator OpenAPI ✓");
    return;
  }

  console.error("codegen --check: snapshot drifts from orchestrator OpenAPI");
  const tmpA = join(tmpdir(), `lumogis-openapi-snap-${process.pid}.json`);
  const tmpB = join(tmpdir(), `lumogis-openapi-gen-${process.pid}.json`);
  try {
    writeFileSync(tmpA, `${JSON.stringify(JSON.parse(a), null, 2)}\n`);
    writeFileSync(tmpB, `${JSON.stringify(JSON.parse(b), null, 2)}\n`);
    const diff = spawnSync("diff", ["-u", tmpA, tmpB], { encoding: "utf-8" });
    if (diff.stdout) {
      console.error(diff.stdout);
    }
    if (diff.stderr) {
      console.error(diff.stderr);
    }
  } finally {
    try {
      unlinkSync(tmpA);
    } catch {
      /* ignore */
    }
    try {
      unlinkSync(tmpB);
    } catch {
      /* ignore */
    }
  }
  console.error("Refresh by running:");
  console.error(
    "  cd orchestrator && python -m scripts.dump_openapi --pretty --sort-keys --out ../clients/lumogis-web/openapi.snapshot.json",
  );
  console.error("Then commit the change.");
  process.exit(1);
}

function canonicalise(text) {
  const obj = JSON.parse(text);
  const info = obj.info;
  if (info && typeof info === "object") {
    obj.info = { ...info, version: "snapshot" };
  }
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
