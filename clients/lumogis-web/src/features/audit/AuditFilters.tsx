// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Activity filters on the shared components (LUM-993 plan B Pass 6). Members get
// pill filters; the household view adds a person picker and, collapsed under
// "More filters", the connector / action-type text filters.

import { ChevronDown } from "lucide-react";

import { SegmentedControl } from "../../components/SegmentedControl";
import { Button } from "../../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import type { UserRow } from "../_shared/UserPicker";
import { AuditLiveToggle } from "./AuditLiveToggle";

export type DatePreset = "24h" | "7d" | "30d" | "custom" | "all";

const DATE_PRESETS: { value: DatePreset; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "custom", label: "Custom" },
  { value: "all", label: "All time" },
];

const EVENT_CHIPS: { label: string; value: string }[] = [
  { label: "All", value: "" },
  { label: "Actions", value: "action.executed" },
  { label: "Privacy", value: "privacy.external_call.denied" },
  { label: "Invites", value: "auth.invite" },
  { label: "Credentials", value: "auth.credential" },
];

const SCOPE_NOTE = "Shared and system audit events are not recorded yet";

export type AuditFiltersScope = "member" | "household";

export interface AuditFiltersProps {
  scope: AuditFiltersScope;
  liveEnabled: boolean;
  onLiveChange: (next: boolean) => void;
  /** Member: date preset row. Household: Refresh beside Live. */
  onRefresh?: () => void;
  liveNote?: string;
  /** Member scope only */
  preset?: DatePreset;
  onPresetChange?: (preset: DatePreset) => void;
  eventType?: string;
  onEventTypeChange?: (value: string) => void;
  afterCustom?: string;
  beforeCustom?: string;
  onAfterCustomChange?: (value: string) => void;
  onBeforeCustomChange?: (value: string) => void;
  /** Household scope only */
  limit?: number;
  onLimitChange?: (limit: number) => void;
  connector?: string;
  onConnectorChange?: (value: string) => void;
  actionTypeFilter?: string;
  onActionTypeFilterChange?: (value: string) => void;
  asUser?: string;
  onAsUserChange?: (userId: string) => void;
  users?: UserRow[];
  usersLoading?: boolean;
  usersError?: boolean;
}

export function AuditFilters({
  scope,
  liveEnabled,
  onLiveChange,
  onRefresh,
  liveNote,
  preset = "7d",
  onPresetChange,
  eventType = "",
  onEventTypeChange,
  afterCustom = "",
  beforeCustom = "",
  onAfterCustomChange,
  onBeforeCustomChange,
  limit = 50,
  onLimitChange,
  connector = "",
  onConnectorChange,
  actionTypeFilter = "",
  onActionTypeFilterChange,
  asUser = "",
  onAsUserChange,
  users,
  usersLoading,
  usersError,
}: AuditFiltersProps): JSX.Element {
  const isMember = scope === "member";
  const statusMessage =
    liveNote ??
    (isMember
      ? "Live tail on — new events appear automatically."
      : "Live tail on — new server activity appears automatically.");

  return (
    <div
      className="mb-4 flex max-w-3xl flex-col gap-3"
      data-testid={isMember ? "audit-member-filters" : "audit-household-filters"}
    >
      <div className="flex flex-wrap items-center gap-2">
        <AuditLiveToggle enabled={liveEnabled} onChange={onLiveChange} />
        {isMember && onPresetChange ? (
          <SegmentedControl
            ariaLabel="Date range"
            value={preset}
            options={DATE_PRESETS}
            onChange={(v) => onPresetChange(v as DatePreset)}
            className="max-w-full flex-wrap"
          />
        ) : null}
        {!isMember && onRefresh ? (
          <Button type="button" variant="outline" size="sm" onClick={onRefresh}>
            Refresh
          </Button>
        ) : null}
        {liveEnabled ? (
          <p className="m-0 text-xs text-muted-foreground" role="status">
            {statusMessage}
          </p>
        ) : null}
      </div>

      {isMember && preset === "custom" && onAfterCustomChange && onBeforeCustomChange ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="audit-after">After</Label>
            <Input
              id="audit-after"
              type="datetime-local"
              value={afterCustom}
              onChange={(e) => onAfterCustomChange(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="audit-before">Before</Label>
            <Input
              id="audit-before"
              type="datetime-local"
              value={beforeCustom}
              onChange={(e) => onBeforeCustomChange(e.target.value)}
            />
          </div>
        </div>
      ) : null}

      {isMember && onEventTypeChange ? (
        <SegmentedControl
          ariaLabel="Event type"
          value={eventType}
          options={EVENT_CHIPS.map((c) => ({ value: c.value, label: c.label }))}
          onChange={onEventTypeChange}
          className="max-w-full flex-wrap"
        />
      ) : null}

      {isMember ? (
        <div className="flex flex-col gap-1.5">
          <SegmentedControl
            ariaLabel="Scope"
            value="personal"
            options={[
              { value: "personal", label: "Personal" },
              { value: "shared", label: "Shared", disabled: true, title: SCOPE_NOTE },
              { value: "system", label: "System", disabled: true, title: SCOPE_NOTE },
            ]}
            onChange={() => {
              /* personal-only until shared/system recording ships */
            }}
            className="max-w-full flex-wrap"
          />
          <p className="m-0 text-xs text-muted-foreground">{SCOPE_NOTE}</p>
        </div>
      ) : null}

      {!isMember && onLimitChange && onConnectorChange && onActionTypeFilterChange ? (
        <>
          {onAsUserChange ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="audit-as-user">View as user</Label>
              {usersLoading ? (
                <span className="text-sm text-muted-foreground">Loading users…</span>
              ) : usersError ? (
                <span className="text-sm text-destructive">Failed to load users.</span>
              ) : (
                <Select value={asUser || "self"} onValueChange={(v) => onAsUserChange(v === "self" ? "" : String(v ?? ""))}>
                  <SelectTrigger id="audit-as-user" aria-label="View as user" className="w-full max-w-md">
                    <SelectValue>
                      {(v: string | null) => {
                        const hit = users?.find((u) => u.id === v);
                        return hit ? `${hit.email}${hit.disabled ? " (turned off)" : ""}` : "Just me";
                      }}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="self">Just me</SelectItem>
                    {users?.map((u) => (
                      <SelectItem key={u.id} value={u.id}>
                        {u.email}
                        {u.disabled ? " (turned off)" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="audit-limit">How many to show (1–200)</Label>
            <Input
              id="audit-limit"
              type="number"
              min={1}
              max={200}
              value={limit}
              onChange={(e) => onLimitChange(Number(e.target.value) || 50)}
              className="w-32"
            />
          </div>
          <Collapsible>
            <CollapsibleTrigger
              render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
            >
              <ChevronDown aria-hidden="true" className="transition-transform group-aria-expanded/trigger:rotate-180" />
              More filters
            </CollapsibleTrigger>
            {/* keepMounted: the two text filters stay in the page while closed. */}
            <CollapsibleContent keepMounted className="grid gap-3 pt-2 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="audit-connector">Connector filter</Label>
                <Input
                  id="audit-connector"
                  type="text"
                  value={connector}
                  onChange={(e) => onConnectorChange(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="audit-action-type">Action type filter</Label>
                <Input
                  id="audit-action-type"
                  type="text"
                  value={actionTypeFilter}
                  onChange={(e) => onActionTypeFilterChange(e.target.value)}
                />
              </div>
            </CollapsibleContent>
          </Collapsible>
        </>
      ) : null}
    </div>
  );
}
