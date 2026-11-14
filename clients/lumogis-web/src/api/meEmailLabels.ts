// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Façade + types for the user-editable email taxonomy + rules (LUM-629).
import type { ApiClient } from "./client";

export interface CategoryItem {
  slug: string;
  label: string;
  enabled: boolean;
  is_custom: boolean;
  is_protected: boolean;
  sort: number;
}

export interface RuleMatch {
  sender?: string[];
  sender_domain?: string[];
  subject_contains?: string[];
  body_contains?: string[];
  bulk_header?: boolean;
}

export interface RuleItem {
  rule_id: string;
  priority: number;
  match: RuleMatch;
  category: string;
  enabled: boolean;
  category_disabled?: boolean;
}

export interface CategoryPatchItem {
  slug: string;
  label?: string | null;
  enabled?: boolean | null;
  sort?: number | null;
}

export interface TaxonomyPatch {
  categories?: CategoryPatchItem[];
  deletes?: string[];
}

export interface ReclassifyResponse {
  slug: string;
  scanned: number;
  moved: number;
  failed: number;
}

const BASE = "/api/v1/me";

export function getTaxonomy(client: ApiClient): Promise<{ categories: CategoryItem[] }> {
  return client.getJson(`${BASE}/email-taxonomy`);
}

export function putTaxonomy(
  client: ApiClient,
  patch: TaxonomyPatch,
): Promise<{ categories: CategoryItem[] }> {
  return client.putJson(`${BASE}/email-taxonomy`, patch);
}

export function getRules(client: ApiClient): Promise<{ rules: RuleItem[] }> {
  return client.getJson(`${BASE}/email-classification-rules`);
}

export function putRules(client: ApiClient, rules: RuleItem[]): Promise<{ rules: RuleItem[] }> {
  return client.putJson(`${BASE}/email-classification-rules`, { rules });
}

export function reclassify(client: ApiClient, slug: string): Promise<ReclassifyResponse> {
  return client.postJson(`${BASE}/email-taxonomy/${encodeURIComponent(slug)}/reclassify`, {});
}
