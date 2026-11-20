// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

/**
 * Files Lumogis looked at for an answer, once each, in the order the server ranked them.
 * Core stores uploads as `<file_id>_<name>`; the id is not shown.
 */
export function sourceNames(citations: ReadonlyArray<{ file_path: string }>): string[] {
  const names: string[] = [];
  for (const c of citations) {
    const base = c.file_path.split("/").pop() ?? c.file_path;
    const name = base.replace(/^[0-9a-f]{32}_/, "");
    if (name !== "" && !names.includes(name)) names.push(name);
  }
  return names;
}
