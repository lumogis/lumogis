// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { Button } from "../../components/Button";
import { SegmentedControl } from "../../components/SegmentedControl";
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
      className="lumogis-audit-filters"
      data-testid={isMember ? "audit-member-filters" : "audit-household-filters"}
    >
      <div className="lumogis-audit-filters__row lumogis-audit-filters__row--time">
        <AuditLiveToggle enabled={liveEnabled} onChange={onLiveChange} />
        {isMember && onPresetChange ? (
          <SegmentedControl
            ariaLabel="Date range"
            value={preset}
            options={DATE_PRESETS}
            onChange={(v) => onPresetChange(v as DatePreset)}
          />
        ) : null}
        {!isMember && onRefresh ? (
          <Button type="button" variant="secondary" size="sm" onClick={onRefresh}>
            Refresh
          </Button>
        ) : null}
        {liveEnabled ? (
          <p className="lumogis-audit-filters__live-note" role="status">
            {statusMessage}
          </p>
        ) : null}
      </div>

      {isMember && preset === "custom" && onAfterCustomChange && onBeforeCustomChange ? (
        <div className="lumogis-audit-filters__custom-range">
          <label className="lumogis-field lumogis-audit-filters__date-field">
            <span className="lumogis-field__label">After</span>
            <input
              type="datetime-local"
              className="lumogis-field__input"
              value={afterCustom}
              onChange={(e) => onAfterCustomChange(e.target.value)}
            />
          </label>
          <label className="lumogis-field lumogis-audit-filters__date-field">
            <span className="lumogis-field__label">Before</span>
            <input
              type="datetime-local"
              className="lumogis-field__input"
              value={beforeCustom}
              onChange={(e) => onBeforeCustomChange(e.target.value)}
            />
          </label>
        </div>
      ) : null}

      {isMember && onEventTypeChange ? (
        <div className="lumogis-audit-filters__row">
          <SegmentedControl
            ariaLabel="Event type"
            value={eventType}
            options={EVENT_CHIPS.map((c) => ({ value: c.value, label: c.label }))}
            onChange={onEventTypeChange}
          />
        </div>
      ) : null}

      {isMember ? (
        <div className="lumogis-audit-filters__scope-block">
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
          />
          <p className="lumogis-audit-filters__scope-note">{SCOPE_NOTE}</p>
        </div>
      ) : null}

      {!isMember && onLimitChange && onConnectorChange && onActionTypeFilterChange ? (
        <div className="lumogis-audit-filters__admin-fields">
          <label className="lumogis-field">
            <span className="lumogis-field__label">Limit (1–200)</span>
            <input
              type="number"
              className="lumogis-field__input"
              min={1}
              max={200}
              value={limit}
              onChange={(e) => onLimitChange(Number(e.target.value) || 50)}
            />
          </label>
          <label className="lumogis-field">
            <span className="lumogis-field__label">Connector filter</span>
            <input
              type="text"
              className="lumogis-field__input"
              value={connector}
              onChange={(e) => onConnectorChange(e.target.value)}
            />
          </label>
          <label className="lumogis-field">
            <span className="lumogis-field__label">Action type filter</span>
            <input
              type="text"
              className="lumogis-field__input"
              value={actionTypeFilter}
              onChange={(e) => onActionTypeFilterChange(e.target.value)}
            />
          </label>
          {onAsUserChange ? (
            <label className="lumogis-field lumogis-field--wide">
              <span className="lumogis-field__label">View as user</span>
              {usersLoading ? (
                <span className="lumogis-field__hint">Loading users…</span>
              ) : usersError ? (
                <span className="lumogis-field__hint lumogis-field__hint--error">Failed to load users.</span>
              ) : (
                <select
                  className="lumogis-field__input lumogis-field__select"
                  value={asUser}
                  onChange={(e) => onAsUserChange(e.target.value)}
                >
                  <option value="">Self (no filter)</option>
                  {users?.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.email} ({u.role}
                      {u.disabled ? ", disabled" : ""})
                    </option>
                  ))}
                </select>
              )}
            </label>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
