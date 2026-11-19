// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Safety test bench (developer page). Rebuilt on the shared components in LUM-993 plan B
// Pass 6: all detail stays visible, now in a Table, Badges, Select/Input/Textarea and Alerts.
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "../../api/client";
import {
  SAFETY_VECTORS,
  type SafetyProbeResult,
  type SafetySuiteResult,
  type SafetyVector,
} from "../../api/safety";
import { useAuth } from "../../auth/AuthProvider";
import { PageSection } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { Textarea } from "../../components/ui/textarea";
import { formatDateTime } from "../../util/formatTimestamp";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  return "Request failed";
}

export function AdminSafetyPlaygroundView(): JSX.Element {
  const { client } = useAuth();
  const [msg, setMsg] = useState<string | null>(null);
  const [vector, setVector] = useState<SafetyVector>("tool_result");
  const [payload, setPayload] = useState<string>("");
  const [actionType, setActionType] = useState<string>("");
  const [probe, setProbe] = useState<SafetyProbeResult | null>(null);

  const runM = useMutation({
    mutationFn: () =>
      client.postJson<Record<string, never>, SafetySuiteResult>("/api/v1/admin/safety/run", {}),
    onSuccess: () => setMsg(null),
    onError: (e) => setMsg(errMsg(e)),
  });

  const probeM = useMutation({
    mutationFn: () =>
      client.postJson<Record<string, string>, SafetyProbeResult>("/api/v1/admin/safety/probe", {
        vector,
        payload,
        action_type: actionType,
      }),
    onSuccess: (r) => {
      setProbe(r);
      setMsg(null);
    },
    onError: (e) => setMsg(errMsg(e)),
  });

  const suite = runM.data;

  return (
    <div>
      <PageSection
        title="Injection test suite"
        description="Run known injection / credential / action-bypass payloads against the live defences (document ingest, retrieval origin-tagging, tool-result scanner, config secrets scanner, hard-limit policy). Runs in dry-run against the detection primitives — nothing is persisted or sent to a model."
      >
        {msg && (
          <Alert variant="destructive" className="mb-3">
            <AlertDescription>{msg}</AlertDescription>
          </Alert>
        )}

        <Button type="button" onClick={() => runM.mutate()} disabled={runM.isPending}>
          {runM.isPending ? "Running…" : "Run injection test suite"}
        </Button>

        {suite && (
          <div className="mt-4 flex flex-col gap-2 text-sm" data-testid="safety-summary">
            <p className="m-0 flex flex-wrap items-center gap-2">
              <span>
                Ran {formatDateTime(suite.ran_at)} — <strong>{suite.passed}/{suite.total} passed</strong>
              </span>
              {suite.failed > 0 && <Badge variant="destructive">{suite.failed} FAILED</Badge>}
              {suite.warnings > 0 && <Badge variant="outline">{suite.warnings} known-gap warning(s)</Badge>}
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Case</TableHead>
                  <TableHead>Vector</TableHead>
                  <TableHead>Expected</TableHead>
                  <TableHead>Actual</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead>Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {suite.results.map((r) => (
                  <TableRow key={r.name} className="align-top">
                    <TableCell className="whitespace-normal">{r.name}</TableCell>
                    <TableCell>{r.vector}</TableCell>
                    <TableCell>{r.expected}</TableCell>
                    <TableCell>{r.actual}</TableCell>
                    <TableCell>
                      {r.passed ? (
                        <Badge variant="secondary">pass</Badge>
                      ) : r.known_gap ? (
                        <Badge variant="outline">warn (known gap)</Badge>
                      ) : (
                        <Badge variant="destructive">FAIL</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-xs whitespace-normal">{r.detail}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </PageSection>

      <PageSection title="Craft a test payload">
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="safety-vector">Vector</Label>
            <Select value={vector} onValueChange={(v) => setVector((v ?? "tool_result") as SafetyVector)}>
              <SelectTrigger id="safety-vector" aria-label="Vector" className="w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SAFETY_VECTORS.map((v) => (
                  <SelectItem key={v} value={v}>
                    {v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {vector === "action_execution" ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="safety-action-type">Action type</Label>
              <Input
                id="safety-action-type"
                value={actionType}
                onChange={(e) => setActionType(e.target.value)}
                placeholder="e.g. mass_communication"
              />
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="safety-payload">Payload</Label>
              <Textarea
                id="safety-payload"
                value={payload}
                onChange={(e) => setPayload(e.target.value)}
                rows={3}
                placeholder="Paste a suspicious payload…"
              />
            </div>
          )}
          <div>
            <Button type="button" onClick={() => probeM.mutate()} disabled={probeM.isPending}>
              {probeM.isPending ? "Probing…" : "Probe"}
            </Button>
          </div>
        </div>

        {probe && (
          <Alert role="status" className="mt-3" data-testid="probe-result">
            <AlertDescription>
              Result: <strong>{probe.actual}</strong>
              {probe.actual === "passed" ? " (defence did not act on this payload)" : ""}
              {probe.detail ? ` — ${probe.detail}` : ""}
            </AlertDescription>
          </Alert>
        )}
      </PageSection>
    </div>
  );
}
