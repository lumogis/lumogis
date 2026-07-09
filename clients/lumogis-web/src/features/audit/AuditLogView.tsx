// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { AuditListResponse } from "../../api/audit";
import { buildAuditStreamUrl, mergeAuditRows } from "../../api/audit";
import { useAuth } from "../../auth/AuthProvider";
import { MeSubshell } from "../me/MeSubshell";
import { AuditLiveToggle } from "./AuditLiveToggle";
import { AuditTable } from "./_shared/AuditTable";
import { useAuditLiveTail } from "./useAuditLiveTail";

type DatePreset = "24h" | "7d" | "30d" | "custom" | "all";

const EVENT_CHIPS: { label: string; value: string }[] = [
  { label: "All", value: "" },
  { label: "Actions", value: "action.executed" },
  { label: "Privacy", value: "privacy.external_call.denied" },
  { label: "Invites", value: "auth.invite" },
  { label: "Credentials", value: "auth.credential" },
];

const PAGE_SIZE = 50;

function presetToAfter(preset: DatePreset): string | undefined {
  if (preset === "all" || preset === "custom") return undefined;
  const now = Date.now();
  const ms =
    preset === "24h" ? 24 * 60 * 60 * 1000 : preset === "7d" ? 7 * 24 * 60 * 60 * 1000 : 30 * 24 * 60 * 60 * 1000;
  return new Date(now - ms).toISOString();
}

export function AuditLogView(): JSX.Element {
  const { client, tokens } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [liveEnabled, setLiveEnabled] = useState(false);

  const eventType = searchParams.get("event_type") ?? "";
  const preset = (searchParams.get("preset") as DatePreset) || "7d";
  const offset = Number(searchParams.get("offset") ?? "0") || 0;
  const afterCustom = searchParams.get("after") ?? "";
  const beforeCustom = searchParams.get("before") ?? "";

  const updateParams = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams);
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const after = useMemo(() => {
    if (preset === "custom" && afterCustom) return afterCustom;
    return presetToAfter(preset);
  }, [preset, afterCustom]);

  const auditUrl = useMemo(() => {
    const p = new URLSearchParams();
    p.set("limit", String(PAGE_SIZE));
    p.set("offset", String(offset));
    if (eventType) p.set("event_type", eventType);
    if (after) p.set("after", after);
    if (preset === "custom" && beforeCustom) p.set("before", beforeCustom);
    return `/api/v1/audit?${p.toString()}`;
  }, [eventType, after, beforeCustom, preset, offset]);

  const listQ = useQuery({
    queryKey: ["member", "audit", auditUrl],
    queryFn: () => client.getJson<AuditListResponse>(auditUrl),
  });

  const baseRows = useMemo(() => listQ.data?.audit ?? [], [listQ.data]);
  const sinceId = baseRows.reduce((max, row) => Math.max(max, row.id), 0);
  const streamUrl = useMemo(
    () =>
      buildAuditStreamUrl({
        sinceId,
        eventType: eventType || undefined,
        after,
        before: preset === "custom" && beforeCustom ? beforeCustom : undefined,
      }),
    [sinceId, eventType, after, beforeCustom, preset],
  );
  const liveRows = useAuditLiveTail({ enabled: liveEnabled, streamUrl, tokens });
  const displayRows = useMemo(() => mergeAuditRows(baseRows, liveRows), [baseRows, liveRows]);

  const total = listQ.data?.total ?? 0;
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;

  return (
    <MeSubshell>
      <section className="lumogis-audit-page">
        <h1>Audit log</h1>
        <p>Recent Lumogis activity for your account.</p>
        <div className="lumogis-dense-form-grid">
          <AuditLiveToggle enabled={liveEnabled} onChange={setLiveEnabled} />
          {liveEnabled ? (
            <p className="lumogis-help-text" role="status">
              Live tail on — new events appear automatically.
            </p>
          ) : null}
        </div>

        <div className="lumogis-dense-form-grid" role="group" aria-label="Date range">
          {(["24h", "7d", "30d", "custom", "all"] as DatePreset[]).map((p) => (
            <button
              key={p}
              type="button"
              aria-pressed={preset === p}
              onClick={() => updateParams({ preset: p === "7d" ? null : p, offset: "0" })}
            >
              {p === "all" ? "All time" : p}
            </button>
          ))}
        </div>

        {preset === "custom" ? (
          <div className="lumogis-dense-form-grid">
            <label>
              After
              <input
                type="datetime-local"
                value={afterCustom}
                onChange={(e) => updateParams({ after: e.target.value || null, offset: "0" })}
              />
            </label>
            <label>
              Before
              <input
                type="datetime-local"
                value={beforeCustom}
                onChange={(e) => updateParams({ before: e.target.value || null, offset: "0" })}
              />
            </label>
          </div>
        ) : null}

        <div className="lumogis-chip-row" role="group" aria-label="Event type">
          {EVENT_CHIPS.map((chip) => (
            <button
              key={chip.label}
              type="button"
              aria-pressed={eventType === chip.value}
              onClick={() =>
                updateParams({ event_type: chip.value || null, offset: "0" })
              }
            >
              {chip.label}
            </button>
          ))}
        </div>

        <fieldset className="lumogis-scope-filters">
          <legend>Scope</legend>
          <label>
            <input type="radio" name="scope" checked readOnly /> Personal
          </label>
          <label title="Shared and system audit events are not recorded yet">
            <input type="radio" name="scope" disabled /> Shared
          </label>
          <label title="Shared and system audit events are not recorded yet">
            <input type="radio" name="scope" disabled /> System
          </label>
          <p className="lumogis-help-text">Shared and system audit events are not recorded yet</p>
        </fieldset>

        <AuditTable
          variant="member"
          rows={displayRows}
          loading={listQ.isPending}
          error={listQ.isError}
          onRetry={() => void listQ.refetch()}
          expandedId={expandedId}
          onToggleExpand={(id) => setExpandedId((cur) => (cur === id ? null : id))}
          markCloudRows
        />

        <div className="lumogis-pagination">
          <button
            type="button"
            disabled={!canPrev}
            onClick={() => updateParams({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}
          >
            Previous
          </button>
          <span>
            {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of {total}
          </span>
          <button
            type="button"
            disabled={!canNext}
            onClick={() => updateParams({ offset: String(offset + PAGE_SIZE) })}
          >
            Next
          </button>
        </div>
      </section>
    </MeSubshell>
  );
}
