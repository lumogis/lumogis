// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Email labels (taxonomy) + classification rules editor (LUM-629), rebuilt on
// the shared components in LUM-993 plan B Pass 4: one row per label with its
// actions in a "…" menu, a name-only "Add a label" (the internal name is made
// from it, labelSlug.ts), and rules written as sentences. Turning a label off
// or deleting it moves its mail to Other on the server
// (services/email_taxonomy.py sweeps disabled/deleted slugs), so both ask first.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

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
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { RowActions } from "../../components/page/RowActions";
import { Alert, AlertDescription } from "../../components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { labelSlug } from "./labelSlug";

type Confirm =
  | { kind: "off"; category: CategoryItem }
  | { kind: "delete"; category: CategoryItem }
  | { kind: "delete-rule"; rule: RuleItem; sentence: string }
  | null;

function errorText(e: unknown): string {
  if (e instanceof ApiError) return typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
  return "Something went wrong. Try again.";
}

export function MeEmailLabelsView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [ruleKeywords, setRuleKeywords] = useState("");
  const [ruleBodyKeywords, setRuleBodyKeywords] = useState("");
  const [ruleCategory, setRuleCategory] = useState("");
  const [editing, setEditing] = useState<{ slug: string; label: string } | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);

  const taxQ = useQuery({
    queryKey: ["email-labels", "taxonomy"],
    queryFn: () => getTaxonomy(client),
  });
  const rulesQ = useQuery({
    queryKey: ["email-labels", "rules"],
    queryFn: () => getRules(client),
  });

  const onErr = (e: unknown) => setError(errorText(e));

  const taxM = useMutation({
    mutationFn: (patch: Parameters<typeof putTaxonomy>[1]) => putTaxonomy(client, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["email-labels"] });
      setError(null);
      toast("Saved.");
    },
    onError: onErr,
  });

  const rulesM = useMutation({
    mutationFn: (rules: RuleItem[]) => putRules(client, rules),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["email-labels", "rules"] });
      setError(null);
      toast("Rules saved.");
    },
    onError: onErr,
  });

  const reclassifyM = useMutation({
    mutationFn: (slug: string) => reclassify(client, slug),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["email-labels"] });
      setError(null);
      toast(`Re-sorted ${labelFor(r.slug)}: ${r.moved} moved of ${r.scanned} checked.`);
    },
    onError: onErr,
  });

  if (taxQ.isPending || rulesQ.isPending) return (
      <LoadingPlaceholder label="Loading your labels…" className="max-w-2xl">
        <SkeletonText lines={6} />
      </LoadingPlaceholder>
    );
  if (taxQ.isError || rulesQ.isError) {
    return (
      <ErrorState
        message="Lumogis couldn't load your labels."
        onRetry={() => {
          void taxQ.refetch();
          void rulesQ.refetch();
        }}
      />
    );
  }

  const categories: CategoryItem[] = taxQ.data?.categories ?? [];
  const rules: RuleItem[] = rulesQ.data?.rules ?? [];
  const enabledCategories = categories.filter((c) => c.enabled);

  function labelFor(slug: string): string {
    return categories.find((c) => c.slug === slug)?.label ?? slug;
  }

  const addLabel = (e: FormEvent) => {
    e.preventDefault();
    const label = newLabel.trim();
    if (!label) return;
    taxM.mutate({
      categories: [{ slug: labelSlug(label, categories.map((c) => c.slug)), label }],
    });
    setNewLabel("");
  };

  const addRule = (e: FormEvent) => {
    e.preventDefault();
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

  const ruleSentence = (r: RuleItem): string => {
    const parts: string[] = [];
    const subj = r.match.subject_contains ?? [];
    const body = r.match.body_contains ?? [];
    if (subj.length) parts.push(`subject contains ${subj.join(", ")}`);
    if (body.length) parts.push(`message text contains ${body.join(", ")}`);
    return `${parts.join(" or ")} → ${labelFor(r.category)}`;
  };

  const doConfirm = () => {
    if (!confirm) return;
    if (confirm.kind === "off") taxM.mutate({ categories: [{ slug: confirm.category.slug, enabled: false }] });
    if (confirm.kind === "delete") taxM.mutate({ deletes: [confirm.category.slug] });
    if (confirm.kind === "delete-rule") {
      rulesM.mutate(rules.filter((r) => r.rule_id !== confirm.rule.rule_id).map(stripResponseFields));
    }
    setConfirm(null);
  };

  return (
    <div className="flex flex-col gap-2">
      {error ? (
        <Alert variant="destructive" className="max-w-2xl">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <PageSection
        title="Labels"
        description="Mail is sorted by your rules first. When no rule matches, the on-device model may suggest one of your own labels."
        info="Rename a label, turn it off, or add your own. Other always stays: mail that fits nowhere else goes there."
      >
        <ul role="list" className="m-0 flex list-none flex-col divide-y divide-border p-0">
          {categories.map((c) => (
            <li key={c.slug} data-slug={c.slug} className="flex min-h-12 flex-wrap items-center gap-2 py-1.5">
              {editing?.slug === c.slug ? (
                <form
                  className="flex flex-1 flex-wrap items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const label = editing.label.trim();
                    if (label) taxM.mutate({ categories: [{ slug: c.slug, label }] });
                    setEditing(null);
                  }}
                >
                  <Input
                    aria-label={`Rename ${c.label}`}
                    value={editing.label}
                    autoFocus
                    onChange={(e) => setEditing({ slug: c.slug, label: e.target.value })}
                    className="min-w-0 flex-1 basis-48"
                  />
                  <Button type="submit" size="sm">
                    Save
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(null)}>
                    Cancel
                  </Button>
                </form>
              ) : (
                <>
                  <span className={c.enabled ? "min-w-0 flex-1 text-sm" : "min-w-0 flex-1 text-sm text-muted-foreground"}>
                    {c.label}
                  </span>
                  {c.is_custom ? <Badge variant="secondary">Your own</Badge> : null}
                  {!c.enabled ? <Badge variant="outline">Off</Badge> : null}
                  <RowActions
                    label={`Actions for ${c.label}`}
                    items={[
                      ...(!c.is_protected
                        ? [
                            { label: "Rename", onSelect: () => setEditing({ slug: c.slug, label: c.label }) },
                            {
                              label: c.enabled ? "Turn off" : "Turn on",
                              onSelect: () =>
                                c.enabled
                                  ? setConfirm({ kind: "off", category: c })
                                  : taxM.mutate({ categories: [{ slug: c.slug, enabled: true }] }),
                            },
                          ]
                        : []),
                      { label: "Re-sort old mail", disabled: !c.enabled, onSelect: () => reclassifyM.mutate(c.slug) },
                      ...(!c.is_protected && c.is_custom
                        ? [{ label: "Delete", destructive: true, onSelect: () => setConfirm({ kind: "delete", category: c }) }]
                        : []),
                    ]}
                  />
                </>
              )}
            </li>
          ))}
        </ul>
      </PageSection>

      <PageSection title="Add a label" description="Give it a name; Lumogis can then sort mail into it.">
        <form className="flex flex-wrap items-end gap-2" onSubmit={addLabel}>
          <div className="flex min-w-0 flex-1 basis-56 flex-col gap-1.5">
            <Label htmlFor="new-label-name">Label name</Label>
            <Input
              id="new-label-name"
              placeholder="For example, Receipts"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={!newLabel.trim() || taxM.isPending}>
            Add label
          </Button>
        </form>
      </PageSection>

      <PageSection
        title="Rules"
        description="A rule puts mail into a label when its subject or message text contains a word you choose."
        info="Rules on message text only work for mailboxes that keep full messages, and on very large syncs they may be skipped. When you re-sort old mail, only subject rules run again."
      >
        {rules.length === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">No rules yet. Add one below.</p>
        ) : (
          <ul role="list" className="m-0 flex list-none flex-col divide-y divide-border p-0">
            {rules.map((r) => {
              const sentence = ruleSentence(r);
              return (
                <li key={r.rule_id} data-rule={r.rule_id} className="flex min-h-12 flex-wrap items-center gap-2 py-1.5">
                  <span className="min-w-0 flex-1 text-sm">{sentence}</span>
                  {r.category_disabled ? <Badge variant="destructive">Label is off</Badge> : null}
                  <RowActions
                    label={`Actions for rule: ${sentence}`}
                    items={[{ label: "Delete rule", destructive: true, onSelect: () => setConfirm({ kind: "delete-rule", rule: r, sentence }) }]}
                  />
                </li>
              );
            })}
          </ul>
        )}

        <form className="mt-4 grid gap-3 border-t pt-4 sm:grid-cols-2" onSubmit={addRule}>
          <h3 className="m-0 text-sm font-semibold sm:col-span-2">Add a rule</h3>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rule-subject">Subject contains</Label>
            <Input
              id="rule-subject"
              placeholder="invoice, receipt"
              value={ruleKeywords}
              onChange={(e) => setRuleKeywords(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rule-body">Message text contains</Label>
            <Input
              id="rule-body"
              placeholder="field trip"
              value={ruleBodyKeywords}
              onChange={(e) => setRuleBodyKeywords(e.target.value)}
            />
          </div>
          <p className="m-0 text-xs text-muted-foreground sm:col-span-2">Separate several words with commas.</p>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rule-label">Put it in</Label>
            <Select value={ruleCategory} onValueChange={(v) => setRuleCategory(String(v ?? ""))}>
              <SelectTrigger id="rule-label" aria-label="Rule label" className="w-full">
                <SelectValue placeholder="Choose a label" />
              </SelectTrigger>
              <SelectContent>
                {enabledCategories.map((c) => (
                  <SelectItem key={c.slug} value={c.slug}>
                    {c.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-end">
            <Button
              type="submit"
              disabled={(!ruleKeywords.trim() && !ruleBodyKeywords.trim()) || !ruleCategory || rulesM.isPending}
            >
              Add rule
            </Button>
          </div>
        </form>
      </PageSection>

      <AlertDialog open={confirm !== null} onOpenChange={(open) => (open ? undefined : setConfirm(null))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirm?.kind === "off"
                ? `Turn off ${confirm.category.label}?`
                : confirm?.kind === "delete"
                  ? `Delete ${confirm.category.label}?`
                  : "Delete this rule?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirm?.kind === "delete-rule"
                ? `${confirm.sentence}. Mail it already sorted stays where it is.`
                : "Mail in this label moves to Other. Turning a label on again doesn't move that mail back."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant={confirm?.kind === "off" ? "default" : "destructiveSolid"} onClick={doConfirm}>
              {confirm?.kind === "off" ? "Turn off" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function stripResponseFields(r: RuleItem): RuleItem {
  // category_disabled is response-only; do not send it back on PUT (extra=forbid).
  const rest = { ...r };
  delete rest.category_disabled;
  return rest;
}
