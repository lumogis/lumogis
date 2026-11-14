// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-627 / LUM-632 — presentation helpers for signal category chips.

/** True when the API resolved a non-empty label worth rendering. */
export function shouldShowCategoryChip(categoryLabel: string | null | undefined): boolean {
  return Boolean(categoryLabel && categoryLabel.trim().length > 0);
}

/** Trim display text; callers must pass `category_label`, never the raw slug. */
export function formatCategoryLabel(categoryLabel: string): string {
  return categoryLabel.trim();
}
