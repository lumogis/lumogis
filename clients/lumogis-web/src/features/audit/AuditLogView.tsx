// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { AuditListResponse } from "../../api/audit";
import { buildAuditStreamUrl, mergeAuditRows } from "../../api/audit";
import { useAuth } from "../../auth/AuthProvider";
import { Button } from "../../components/Button";
import { MeSubshell } from "../me/MeSubshell";
import { AuditFilters, type DatePreset } from "./AuditFilters";
import { AuditTable } from "./_shared/AuditTable";
import { useAuditLiveTail } from "./useAuditLiveTail";

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
        <h1>My activity</h1>
        <p className="lumogis-prose-mono">Recent Lumogis activity for your account.</p>

        <AuditFilters
          scope="member"
          liveEnabled={liveEnabled}
          onLiveChange={setLiveEnabled}
          preset={preset}
          onPresetChange={(p) => updateParams({ preset: p === "7d" ? null : p, offset: "0" })}
          eventType={eventType}
          onEventTypeChange={(v) => updateParams({ event_type: v || null, offset: "0" })}
          afterCustom={afterCustom}
          beforeCustom={beforeCustom}
          onAfterCustomChange={(v) => updateParams({ after: v || null, offset: "0" })}
          onBeforeCustomChange={(v) => updateParams({ before: v || null, offset: "0" })}
        />

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
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={!canPrev}
            onClick={() => updateParams({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}
          >
            Previous
          </Button>
          <span>
            {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of {total}
          </span>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={!canNext}
            onClick={() => updateParams({ offset: String(offset + PAGE_SIZE) })}
          >
            Next
          </Button>
        </div>
      </section>
    </MeSubshell>
  );
}
