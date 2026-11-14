// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Email labels (taxonomy) + classification rules editor (LUM-629).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import {
  getRules,
  getTaxonomy,
  putRules,
  putTaxonomy,
  reclassify,
  type CategoryItem,
  type RuleItem,
} from "../../api/meEmailLabels";

export function MeEmailLabelsView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [newSlug, setNewSlug] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [ruleKeywords, setRuleKeywords] = useState("");
  const [ruleBodyKeywords, setRuleBodyKeywords] = useState("");
  const [ruleCategory, setRuleCategory] = useState("");
  const [editing, setEditing] = useState<{ slug: string; label: string } | null>(null);

  const taxQ = useQuery({
    queryKey: ["email-labels", "taxonomy"],
    queryFn: () => getTaxonomy(client),
  });
  const rulesQ = useQuery({
    queryKey: ["email-labels", "rules"],
    queryFn: () => getRules(client),
  });

  const onErr = (e: unknown) =>
    setMsg(e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)) : "Failed");

  const taxM = useMutation({
    mutationFn: (patch: Parameters<typeof putTaxonomy>[1]) => putTaxonomy(client, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["email-labels"] });
      setMsg("Saved.");
    },
    onError: onErr,
  });

  const rulesM = useMutation({
    mutationFn: (rules: RuleItem[]) => putRules(client, rules),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["email-labels", "rules"] });
      setMsg("Rules saved.");
    },
    onError: onErr,
  });

  const reclassifyM = useMutation({
    mutationFn: (slug: string) => reclassify(client, slug),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["email-labels"] });
      setMsg(`Reclassified ${slug_label(r.slug)}: ${r.moved} moved, ${r.scanned} scanned.`);
    },
    onError: onErr,
  });

  if (taxQ.isPending || rulesQ.isPending) return <p>Loading…</p>;

  const categories: CategoryItem[] = taxQ.data?.categories ?? [];
  const rules: RuleItem[] = rulesQ.data?.rules ?? [];
  const enabledCategories = categories.filter((c) => c.enabled);

  const addCustom = () => {
    if (!newSlug || !newLabel) return;
    taxM.mutate({ categories: [{ slug: newSlug, label: newLabel }] });
    setNewSlug("");
    setNewLabel("");
  };

  const addRule = () => {
    const kws = ruleKeywords.split(",").map((s) => s.trim()).filter(Boolean);
    const bodyKws = ruleBodyKeywords.split(",").map((s) => s.trim()).filter(Boolean);
    if ((!kws.length && !bodyKws.length) || !ruleCategory) return;
    const rule_id = `r_${Date.now().toString(36)}`;
    const match: { subject_contains?: string[]; body_contains?: string[] } = {};
    if (kws.length) match.subject_contains = kws;
    if (bodyKws.length) match.body_contains = bodyKws;
    rulesM.mutate([
      ...rules.map(stripResponseFields),
      { rule_id, priority: 50, match, category: ruleCategory, enabled: true },
    ]);
    setRuleKeywords("");
    setRuleBodyKeywords("");
    setRuleCategory("");
  };

  const deleteRule = (rule_id: string) =>
    rulesM.mutate(rules.filter((r) => r.rule_id !== rule_id).map(stripResponseFields));

  return (
    <section className="lumogis-email-labels">
      <h2>Email labels</h2>
      <p className="lumogis-lead">
        Your household&rsquo;s email categories. Rename or disable the defaults, or add your own.
        Mail is filed by your rules first; the on-device model may also propose a category for a
        custom label when no rule matches.
      </p>
      {msg && <p role="status" className="lumogis-inline-msg">{msg}</p>}

      <h3>Categories</h3>
      <ul className="lumogis-label-list">
        {categories.map((c) => (
          <li key={c.slug} data-slug={c.slug}>
            {editing?.slug === c.slug ? (
              <>
                <input
                  aria-label={`Rename ${c.label}`}
                  value={editing.label}
                  onChange={(e) => setEditing({ slug: c.slug, label: e.target.value })}
                />
                <button
                  type="button"
                  onClick={() => {
                    taxM.mutate({ categories: [{ slug: c.slug, label: editing.label }] });
                    setEditing(null);
                  }}
                >
                  Save
                </button>
                <button type="button" onClick={() => setEditing(null)}>Cancel</button>
              </>
            ) : (
              <span className="lumogis-label-list__name">{c.label}</span>
            )}
            {c.is_custom && <span className="lumogis-badge">custom</span>}
            {!c.enabled && <span className="lumogis-badge lumogis-badge--muted">disabled</span>}
            {!c.is_protected && editing?.slug !== c.slug && (
              <>
                <button type="button" onClick={() => setEditing({ slug: c.slug, label: c.label })}>
                  Rename
                </button>
                <button
                  type="button"
                  onClick={() => taxM.mutate({ categories: [{ slug: c.slug, enabled: !c.enabled }] })}
                >
                  {c.enabled ? "Disable" : "Enable"}
                </button>
                {c.is_custom && (
                  <button type="button" onClick={() => taxM.mutate({ deletes: [c.slug] })}>
                    Delete
                  </button>
                )}
              </>
            )}
            <button
              type="button"
              aria-label={`Reclassify ${c.label}`}
              disabled={!c.enabled}
              onClick={() => reclassifyM.mutate(c.slug)}
            >
              Reclassify
            </button>
          </li>
        ))}
      </ul>

      <h3>Add a category</h3>
      <div className="lumogis-add-category">
        <input
          aria-label="New category slug"
          placeholder="slug (e.g. receipts)"
          value={newSlug}
          onChange={(e) => setNewSlug(e.target.value)}
        />
        <input
          aria-label="New category label"
          placeholder="Label"
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
        />
        <button type="button" onClick={addCustom}>Add</button>
      </div>

      <h3>Rules</h3>
      <p className="lumogis-lead">
        Keyword rules route matching mail into a category. Subject and sender rules apply to all
        mail; <strong>body</strong> rules apply only to fully-ingested mail (best-effort on large
        sync bursts; plain-text bodies). When you reclassify old mail, only subject rules re-run
        &mdash; sender and body rules don&rsquo;t.
      </p>
      <ul className="lumogis-rule-list">
        {rules.map((r) => (
          <li key={r.rule_id} data-rule={r.rule_id}>
            <span>
              {(r.match.subject_contains ?? []).length > 0 &&
                `subject contains ${(r.match.subject_contains ?? []).join(", ")} `}
              {(r.match.body_contains ?? []).length > 0 &&
                `body contains ${(r.match.body_contains ?? []).join(", ")} `}
              &rarr; {r.category}
            </span>
            {r.category_disabled && (
              <span className="lumogis-badge lumogis-badge--warn">category disabled</span>
            )}
            <button type="button" onClick={() => deleteRule(r.rule_id)}>Delete</button>
          </li>
        ))}
      </ul>
      <div className="lumogis-add-rule">
        <input
          aria-label="Rule subject keywords"
          placeholder="subject keywords, comma-separated"
          value={ruleKeywords}
          onChange={(e) => setRuleKeywords(e.target.value)}
        />
        <input
          aria-label="Rule body keywords"
          placeholder="body keywords (fully-ingested mail only)"
          value={ruleBodyKeywords}
          onChange={(e) => setRuleBodyKeywords(e.target.value)}
        />
        <select
          aria-label="Rule category"
          value={ruleCategory}
          onChange={(e) => setRuleCategory(e.target.value)}
        >
          <option value="">Choose category…</option>
          {enabledCategories.map((c) => (
            <option key={c.slug} value={c.slug}>{c.label}</option>
          ))}
        </select>
        <button type="button" onClick={addRule}>Add rule</button>
      </div>
    </section>
  );

  function slug_label(slug: string): string {
    return categories.find((c) => c.slug === slug)?.label ?? slug;
  }
}

function stripResponseFields(r: RuleItem): RuleItem {
  // category_disabled is response-only; do not send it back on PUT (extra=forbid).
  const rest = { ...r };
  delete rest.category_disabled;
  return rest;
}
