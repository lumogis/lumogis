#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * LUM-429 — validate TEST-COVERAGE-MATRIX-* docs vs scripts/feature-ids.json.
 *
 * Usage:
 *   node scripts/check-coverage-matrix.mjs
 *   node scripts/check-coverage-matrix.mjs --write-catalog
 *   node scripts/check-coverage-matrix.mjs --matrix path/to/matrix.md [--catalog path.json] [--skip-audit-header]
 */
import { access, readFile, writeFile } from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  MATRIX_FILES,
  MATRIX_PREFIX,
  buildCatalog,
  checkAuditHeader,
  checkLegend,
  parseMatrix,
  validateSync,
} from "./lib/coverage-matrix-parser.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..");
const defaultCatalogPath = join(repoRoot, "scripts/feature-ids.json");

function usage() {
  return `Usage:
  node scripts/check-coverage-matrix.mjs [--write-catalog]
  node scripts/check-coverage-matrix.mjs --matrix FILE [--catalog FILE] [--skip-audit-header]`;
}

function parseArgs(argv) {
  let writeCatalog = false;
  let matrixPath = null;
  let catalogPath = defaultCatalogPath;
  let skipAuditHeader = false;

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") {
      console.log(usage());
      process.exit(0);
    }
    if (arg === "--write-catalog") {
      writeCatalog = true;
      continue;
    }
    if (arg === "--skip-audit-header") {
      skipAuditHeader = true;
      continue;
    }
    if (arg === "--matrix") {
      matrixPath = argv[++i];
      if (!matrixPath) throw new Error("--matrix requires a path");
      continue;
    }
    if (arg === "--catalog") {
      catalogPath = argv[++i];
      if (!catalogPath) throw new Error("--catalog requires a path");
      continue;
    }
    throw new Error(`unknown argument: ${arg}`);
  }

  return { writeCatalog, matrixPath, catalogPath, skipAuditHeader };
}

/**
 * @param {string} relPath
 * @param {string | null} matrixKey
 */
async function fileExists(absPath) {
  try {
    await access(absPath, fsConstants.R_OK);
    return true;
  } catch {
    return false;
  }
}

async function loadMatrixFile(relPath, matrixKey) {
  const abs = resolve(repoRoot, relPath);
  if (!(await fileExists(abs))) {
    return { relPath, parsed: { rows: [], errors: [] }, errors: [], missing: true };
  }
  const markdown = await readFile(abs, "utf8");
  const expectedPrefix = matrixKey ? MATRIX_PREFIX[matrixKey] : undefined;
  const parsed = parseMatrix(markdown, { matrixKey, expectedPrefix });
  const errors = [...parsed.errors];
  if (!skipAuditHeader) errors.push(...checkAuditHeader(markdown));
  errors.push(...checkLegend(markdown));
  return { relPath, parsed, errors, missing: false };
}

let skipAuditHeader = false;

async function loadAllMatrices() {
  /** @type {import('./lib/coverage-matrix-parser.mjs').MatrixRow[]} */
  const allRows = [];
  const allErrors = [];
  /** @type {Set<string>} */
  const loadedPrefixes = new Set();

  for (const [key, relPath] of Object.entries(MATRIX_FILES)) {
    const { parsed, errors, missing } = await loadMatrixFile(relPath, key);
    if (missing) continue;
    loadedPrefixes.add(MATRIX_PREFIX[key]);
    if (errors.length) {
      allErrors.push(`${relPath}:`);
      for (const err of errors) allErrors.push(`  - ${err}`);
    }
    allRows.push(...parsed.rows);
  }

  return { allRows, allErrors, loadedPrefixes };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  skipAuditHeader = args.skipAuditHeader;

  if (args.matrixPath) {
    const abs = resolve(process.cwd(), args.matrixPath);
    const markdown = await readFile(abs, "utf8");
    const parsed = parseMatrix(markdown);
    const errors = [...parsed.errors];
    if (!args.skipAuditHeader) errors.push(...checkAuditHeader(markdown));
    errors.push(...checkLegend(markdown));

    let catalogIds = [];
    if (args.writeCatalog) {
      const catalog = buildCatalog(parsed.rows);
      await writeFile(resolve(args.catalogPath), `${JSON.stringify(catalog, null, 2)}\n`);
      console.log(`Wrote ${args.catalogPath} (${catalog.ids.length} IDs)`);
      process.exit(0);
    }

    try {
      const raw = await readFile(resolve(args.catalogPath), "utf8");
      const catalog = JSON.parse(raw);
      catalogIds = catalog.ids ?? [];
    } catch (err) {
      console.error(`check-coverage-matrix: failed to read catalog: ${err.message}`);
      process.exit(1);
    }

    const validation = validateSync(parsed.rows, catalogIds);
    let failed = errors.length > 0;
    if (failed) {
      console.error("Matrix errors:");
      for (const e of errors) console.error(` - ${e}`);
    }
    reportValidation(validation, parsed.rows.length, catalogIds.length, failed);
    process.exit(failed ? 1 : 0);
  }

  const { allRows, allErrors, loadedPrefixes } = await loadAllMatrices();

  if (args.writeCatalog) {
    const expected = Object.keys(MATRIX_FILES).length;
    if (loadedPrefixes.size !== expected) {
      console.error(
        `check-coverage-matrix: --write-catalog requires all ${expected} matrix files; ` +
          `found ${loadedPrefixes.size} (missing private trees?)`,
      );
      process.exit(1);
    }
    const catalog = buildCatalog(allRows);
    const out = join(repoRoot, "scripts/feature-ids.json");
    await writeFile(out, `${JSON.stringify(catalog, null, 2)}\n`);
    console.log(`Wrote ${out} (${catalog.ids.length} IDs)`);
    process.exit(allErrors.length ? 1 : 0);
  }

  let catalog;
  try {
    catalog = JSON.parse(await readFile(defaultCatalogPath, "utf8"));
  } catch (err) {
    console.error(`check-coverage-matrix: failed to read feature-ids.json: ${err.message}`);
    console.error("Run: node scripts/check-coverage-matrix.mjs --write-catalog");
    process.exit(1);
  }

  if (!Array.isArray(catalog.ids)) {
    console.error("check-coverage-matrix: feature-ids.json missing `ids` array");
    process.exit(1);
  }

  const validation = validateSync(allRows, catalog.ids, loadedPrefixes);
  let failed = allErrors.length > 0;
  if (loadedPrefixes.size < Object.keys(MATRIX_FILES).length) {
    console.log(
      `Note: ${Object.keys(MATRIX_FILES).length - loadedPrefixes.size} private matrix file(s) absent — ` +
        `catalog IDs for missing trees skipped (public-export layout).`,
    );
  }

  if (allErrors.length) {
    console.error("Matrix file errors:");
    for (const e of allErrors) console.error(e.startsWith("  ") ? e : ` ${e}`);
  }

  failed = reportValidation(validation, allRows.length, catalog.ids.length, failed) || failed;
  process.exit(failed ? 1 : 0);
}

/**
 * @param {ReturnType<typeof validateSync>} validation
 * @param {number} rowCount
 * @param {number} catalogCount
 * @param {boolean} alreadyFailed
 */
function reportValidation(validation, rowCount, catalogCount, alreadyFailed) {
  let failed = alreadyFailed;

  if (validation.duplicates.length) {
    console.error("Duplicate IDs across matrices:");
    for (const id of validation.duplicates) console.error(` - ${id}`);
    failed = true;
  }
  if (validation.missingFromMatrix.length) {
    console.error("Catalog IDs missing from matrices:");
    for (const id of validation.missingFromMatrix) console.error(` - ${id}`);
    failed = true;
  }
  if (validation.missingFromCatalog.length) {
    console.error("Matrix IDs missing from catalog (run --write-catalog):");
    for (const id of validation.missingFromCatalog) console.error(` - ${id}`);
    failed = true;
  }

  console.log(
    `Matrices: ${rowCount} rows (${validation.uniqueRows} unique), ${catalogCount} catalog IDs, ` +
      `${validation.duplicates.length} duplicates, ` +
      `${validation.missingFromMatrix.length} missing from matrix, ` +
      `${validation.missingFromCatalog.length} missing from catalog`,
  );
  return failed;
}

main().catch((err) => {
  console.error(`check-coverage-matrix: ${err.message}`);
  process.exit(1);
});
