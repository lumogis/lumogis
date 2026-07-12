// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only detail for a committed (indexed) capture (LUM-607). Renders the
// immutable provenance snapshot — NO edit / delete / attach / re-commit
// controls (deliberately not CaptureDetailPanel). Destructive actions are also
// rejected by the API (409 on indexed) — this UI simply never offers them.

import { useQuery } from "@tanstack/react-query";

import { ApiError } from "../../api/client";
import type { ApiClient } from "../../api/client";
import { formatCaptureErrorMessage, getCapture } from "../../api/captures";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return formatCaptureErrorMessage(e.status, e.detail);
  if (e instanceof Error) return e.message;
  return "Request failed";
}

// Defence-in-depth: only render a live link for http(s) URLs. The API already
// enforces this scheme allowlist at write time (_validate_url), but this guards
// legacy rows or any future bypass so a javascript:/data: URI is shown as text.
function safeHttpUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const scheme = new URL(url).protocol;
    return scheme === "http:" || scheme === "https:" ? url : null;
  } catch {
    return null;
  }
}

export function CaptureArchiveDetail({
  client,
  captureId,
  onClose,
}: {
  client: ApiClient;
  captureId: string;
  onClose: () => void;
}): JSX.Element {
  const q = useQuery({
    queryKey: ["captures", "detail", captureId],
    queryFn: () => getCapture(client, captureId),
  });

  return (
    <section
      aria-label="Committed capture"
      style={{ border: "1px solid #bdbdbd", borderRadius: 8, padding: "1rem", marginBottom: "1rem" }}
    >
      {q.isPending ? (
        <p>Loading…</p>
      ) : q.isError ? (
        <p role="alert">{errMsg(q.error)}</p>
      ) : (
        <>
          {q.data.title ? <h3 style={{ marginTop: 0 }}>{q.data.title}</h3> : null}
          {q.data.text ? (
            <p style={{ whiteSpace: "pre-wrap" }}>{q.data.text}</p>
          ) : null}
          {q.data.url ? (
            <p>
              {safeHttpUrl(q.data.url) ? (
                <a href={safeHttpUrl(q.data.url)!} target="_blank" rel="noopener noreferrer">
                  {q.data.url}
                </a>
              ) : (
                <span>{q.data.url}</span>
              )}
            </p>
          ) : null}
          {q.data.tags && q.data.tags.length > 0 ? (
            <p style={{ fontSize: "0.85rem", opacity: 0.85 }}>Tags: {q.data.tags.join(", ")}</p>
          ) : null}

          {q.data.attachments.length > 0 ? (
            <div style={{ fontSize: "0.85rem" }}>
              <strong>Attachments</strong>
              <ul>
                {q.data.attachments.map((a) => (
                  <li key={a.id}>
                    {a.attachment_type} · {a.original_filename ?? a.id}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {q.data.transcripts.length > 0 ? (
            <div style={{ fontSize: "0.85rem" }}>
              <strong>Transcripts</strong>
              <ul>
                {q.data.transcripts.map((t) => (
                  <li key={t.id} style={{ whiteSpace: "pre-wrap" }}>
                    {t.transcript_text ?? `(${t.transcript_status})`}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <hr style={{ opacity: 0.3, margin: "0.75rem 0" }} />
          <p style={{ fontSize: "0.85rem", opacity: 0.85 }}>
            {q.data.note_id
              ? `Committed to memory (note ${q.data.note_id.slice(0, 8)}…).`
              : "Committed to memory."}
            {q.data.indexed_at ? ` Committed ${new Date(q.data.indexed_at).toLocaleString()}.` : ""}
          </p>
          <p style={{ fontSize: "0.8rem", opacity: 0.7 }}>
            Find it through chat context or search. (A direct link from here arrives with search
            parity.)
          </p>
        </>
      )}

      <button type="button" onClick={onClose}>
        Close
      </button>
    </section>
  );
}
