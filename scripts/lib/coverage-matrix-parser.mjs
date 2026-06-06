// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/** @typedef {{ id: string, feature: string, testSources: string, layer: string, status: string, notes: string, line: number, matrixKey?: string }} MatrixRow */

/** Lumogis: 1.x.y or 1.x.y.z with top-level prefix 1–4 (LUM-384). */
export const ID_REGEX = /^[1-4]\.\d+\.\d+(?:\.\d+)?$/;

const ROW_REGEX =
  /^\|\s*([1-4](?:\.\d+){2,3})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$/u;

export const VALID_STATUS = new Set(["✅", "🟡", "❌", "🚫"]);

export const MATRIX_FILES = {
  core: "docs/testing/TEST-COVERAGE-MATRIX-core.md",
  web: "docs/testing/TEST-COVERAGE-MATRIX-web.md",
  kg: "docs/private/testing/TEST-COVERAGE-MATRIX-kg.md",
  desktop: "docs/private/testing/TEST-COVERAGE-MATRIX-hub.md",
};

/** @type {Record<string, string>} */
export const MATRIX_PREFIX = {
  core: "1",
  web: "2",
  kg: "3",
  desktop: "4",
};

/**
 * @param {string} markdown
 * @param {{ matrixKey?: string, expectedPrefix?: string }} [opts]
 */
export function parseMatrix(markdown, opts = {}) {
  if (typeof markdown !== "string") {
    return { rows: [], errors: ["Input must be a string"] };
  }

  const { matrixKey, expectedPrefix } = opts;
  /** @type {MatrixRow[]} */
  const rows = [];
  /** @type {string[]} */
  const errors = [];
  const lines = markdown.split(/\r?\n/);

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const match = ROW_REGEX.exec(line);
    if (!match) continue;

    const [, id, feature, testSources, layer, rawStatus, notes] = match;
    const status = rawStatus.trim();
    const lineNo = i + 1;

    if (!ID_REGEX.test(id)) {
      errors.push(`Line ${lineNo}: invalid ID format "${id}"`);
      continue;
    }

    if (expectedPrefix && !id.startsWith(`${expectedPrefix}.`)) {
      errors.push(
        `Line ${lineNo} (${id}): ID must use prefix ${expectedPrefix}.x.y for this matrix`,
      );
      continue;
    }

    if (!VALID_STATUS.has(status)) {
      errors.push(`Line ${lineNo} (${id}): invalid status "${status}"`);
      continue;
    }

    if (status === "🚫" && !notes.includes("MS-TBD")) {
      errors.push(
        `Line ${lineNo} (${id}): 🚫 rows must mention MS-TBD in Notes (LUM-385 stub)`,
      );
    }

    rows.push({
      id,
      feature: feature.trim(),
      testSources: testSources.trim(),
      layer: layer.trim(),
      status,
      notes: notes.trim(),
      line: lineNo,
      matrixKey,
    });
  }

  return { rows, errors };
}

/**
 * @param {string} markdown
 */
export function checkAuditHeader(markdown) {
  const head = markdown.split(/\r?\n/).slice(0, 8).join("\n");
  if (!/<!--\s*Last audited:/i.test(head)) {
    return ["Missing `<!-- Last audited: ... -->` header in first 8 lines"];
  }
  return [];
}

/**
 * @param {string} markdown
 */
export function checkLegend(markdown) {
  const errors = [];
  for (const sym of VALID_STATUS) {
    if (!markdown.includes(sym)) {
      errors.push(`Legend or body missing status symbol ${sym}`);
    }
  }
  return errors;
}

/**
 * @param {string} id
 * @param {Set<string>} loadedPrefixes — top-level digits "1".."4" for matrices on disk
 */
export function catalogIdInScope(id, loadedPrefixes) {
  const prefix = id.split(".", 1)[0];
  return loadedPrefixes.has(prefix);
}

/**
 * @param {MatrixRow[]} rows
 * @param {string[]} catalogIds
 * @param {Set<string>} [loadedPrefixes] — when set, ignore catalog IDs for absent trees (public export)
 */
export function validateSync(rows, catalogIds, loadedPrefixes) {
  const counts = new Map();
  for (const { id } of rows) {
    counts.set(id, (counts.get(id) ?? 0) + 1);
  }

  const duplicates = [];
  for (const [id, count] of counts) {
    if (count > 1) duplicates.push(id);
  }

  const catalogSet =
    catalogIds instanceof Set ? catalogIds : new Set(catalogIds);
  const rowIds = new Set(counts.keys());

  const missingFromMatrix = [];
  for (const id of catalogSet) {
    if (loadedPrefixes && !catalogIdInScope(id, loadedPrefixes)) continue;
    if (!rowIds.has(id)) missingFromMatrix.push(id);
  }

  const missingFromCatalog = [];
  for (const id of rowIds) {
    if (!catalogSet.has(id)) missingFromCatalog.push(id);
  }

  duplicates.sort();
  missingFromMatrix.sort(compareIds);
  missingFromCatalog.sort(compareIds);

  return {
    duplicates,
    missingFromMatrix,
    missingFromCatalog,
    totalRows: rows.length,
    uniqueRows: rowIds.size,
  };
}

/**
 * @param {string} a
 * @param {string} b
 */
export function compareIds(a, b) {
  const pa = a.split(".").map((x) => Number.parseInt(x, 10));
  const pb = b.split(".").map((x) => Number.parseInt(x, 10));
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i++) {
    const da = pa[i] ?? 0;
    const db = pb[i] ?? 0;
    if (da !== db) return da - db;
  }
  return 0;
}

/**
 * @param {MatrixRow[]} rows
 */
export function buildCatalog(rows) {
  const ids = [...new Set(rows.map((r) => r.id))].sort(compareIds);
  return {
    schema: "lumogis-feature-ids/v1",
    generated_at: new Date().toISOString(),
    generator: "scripts/check-coverage-matrix.mjs --write-catalog",
    row_count: ids.length,
    matrices: { ...MATRIX_FILES },
    ids,
  };
}
