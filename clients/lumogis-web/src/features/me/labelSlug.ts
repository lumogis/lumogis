// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The internal name of a new email label, made from the name a person types
// (LUM-993 plan B). People never see or type it any more. It must match the
// server's rule, `^[a-z][a-z0-9_]{0,31}$` (services/email_taxonomy.py
// `_SLUG_RE`), and must not clash with a label that exists.

const MAX = 32;

export function labelSlug(name: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  let base = name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "") // accents off: "Rechnungen ä" → "rechnungen a"
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^[^a-z]+/, "") // must start with a letter
    .replace(/_+$/, "")
    .slice(0, MAX)
    .replace(/_+$/, "");
  if (!base) base = "label";
  if (!used.has(base) && base !== "label") return base;
  for (let n = 2; ; n++) {
    const suffix = `_${n}`;
    const candidate = `${base.slice(0, MAX - suffix.length)}${suffix}`;
    if (!used.has(candidate)) return candidate;
  }
}
