// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation } from "@tanstack/react-query";
import { type CSSProperties, useState } from "react";
import { ApiError } from "../../api/client";
import {
  SAFETY_VECTORS,
  type SafetyProbeResult,
  type SafetySuiteResult,
  type SafetyVector,
} from "../../api/safety";
import { useAuth } from "../../auth/AuthProvider";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  return "Request failed";
}

const STYLE_PASS: CSSProperties = { color: "#137333" };
const STYLE_WARN: CSSProperties = { color: "#b06000" };
const STYLE_FAIL: CSSProperties = { color: "#c5221f", fontWeight: 600 };

function rowStyle(passed: boolean, knownGap: boolean): CSSProperties {
  if (passed) return STYLE_PASS;
  return knownGap ? STYLE_WARN : STYLE_FAIL;
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
    <section aria-labelledby="safety-heading">
      <h2 id="safety-heading">Safety playground</h2>
      <p>
        Run known injection / credential / action-bypass payloads against the live defences
        (document ingest, retrieval origin-tagging, tool-result scanner, config secrets scanner,
        hard-limit policy). Runs in dry-run against the detection primitives — nothing is persisted
        or sent to a model.
      </p>

      {msg && <p role="alert">{msg}</p>}

      <div className="safety-actions">
        <button type="button" onClick={() => runM.mutate()} disabled={runM.isPending}>
          {runM.isPending ? "Running…" : "Run injection test suite"}
        </button>
      </div>

      {suite && (
        <div className="safety-summary" data-testid="safety-summary">
          <p>
            Ran {suite.ran_at} — <strong>{suite.passed}/{suite.total} passed</strong>
            {suite.failed > 0 && <span style={STYLE_FAIL}> · {suite.failed} FAILED</span>}
            {suite.warnings > 0 && (
              <span style={STYLE_WARN}> · {suite.warnings} known-gap warning(s)</span>
            )}
          </p>
          <table>
            <thead>
              <tr>
                <th>Case</th>
                <th>Vector</th>
                <th>Expected</th>
                <th>Actual</th>
                <th>Result</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {suite.results.map((r) => (
                <tr key={r.name} style={rowStyle(r.passed, r.known_gap)}>
                  <td>{r.name}</td>
                  <td>{r.vector}</td>
                  <td>{r.expected}</td>
                  <td>{r.actual}</td>
                  <td>
                    {r.passed ? "pass" : r.known_gap ? "warn (known gap)" : "FAIL"}
                  </td>
                  <td>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <hr />

      <h3>Craft a test payload</h3>
      <div className="safety-probe">
        <label>
          Vector{" "}
          <select value={vector} onChange={(e) => setVector(e.target.value as SafetyVector)}>
            {SAFETY_VECTORS.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
        {vector === "action_execution" ? (
          <label>
            Action type{" "}
            <input
              value={actionType}
              onChange={(e) => setActionType(e.target.value)}
              placeholder="e.g. mass_communication"
            />
          </label>
        ) : (
          <label>
            Payload
            <textarea
              value={payload}
              onChange={(e) => setPayload(e.target.value)}
              rows={3}
              placeholder="Paste a suspicious payload…"
            />
          </label>
        )}
        <button type="button" onClick={() => probeM.mutate()} disabled={probeM.isPending}>
          {probeM.isPending ? "Probing…" : "Probe"}
        </button>
      </div>

      {probe && (
        <p data-testid="probe-result" style={probe.actual === "passed" ? STYLE_WARN : STYLE_PASS}>
          Result: <strong>{probe.actual}</strong>
          {probe.actual === "passed" ? " (defence did not act on this payload)" : ""}
          {probe.detail ? ` — ${probe.detail}` : ""}
        </p>
      )}
    </section>
  );
}
