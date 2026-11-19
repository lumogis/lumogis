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
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { formatDateTime } from "../../util/formatTimestamp";

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
    <section aria-label="Committed capture" className="mb-4">
      <Card>
        <CardContent className="flex flex-col gap-3">
          {q.isPending ? (
            <LoadingPlaceholder label="Loading…">
              <SkeletonText lines={3} />
            </LoadingPlaceholder>
          ) : q.isError ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{errMsg(q.error)}</AlertDescription>
            </Alert>
          ) : (
            <>
              {q.data.title ? <h3 className="m-0 text-base font-semibold">{q.data.title}</h3> : null}
              {q.data.text ? <p className="m-0 text-sm whitespace-pre-wrap">{q.data.text}</p> : null}
              {q.data.url ? (
                <p className="m-0 text-sm break-all">
                  {safeHttpUrl(q.data.url) ? (
                    <a
                      href={safeHttpUrl(q.data.url)!}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent-text underline underline-offset-2"
                    >
                      {q.data.url}
                    </a>
                  ) : (
                    <span>{q.data.url}</span>
                  )}
                </p>
              ) : null}
              {q.data.tags && q.data.tags.length > 0 ? (
                <p className="m-0 text-sm text-muted-foreground">Tags: {q.data.tags.join(", ")}</p>
              ) : null}

              {q.data.attachments.length > 0 ? (
                <div className="text-sm">
                  <h4 className="m-0 mb-1 text-sm font-semibold">Attachments</h4>
                  <ul className="m-0 list-none p-0">
                    {q.data.attachments.map((a) => (
                      <li key={a.id}>
                        {a.attachment_type} · {a.original_filename ?? a.id}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {q.data.transcripts.length > 0 ? (
                <div className="text-sm">
                  <h4 className="m-0 mb-1 text-sm font-semibold">Transcripts</h4>
                  <ul className="m-0 list-none p-0">
                    {q.data.transcripts.map((t) => (
                      <li key={t.id} className="whitespace-pre-wrap">
                        {t.transcript_text ?? `(${t.transcript_status})`}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="border-t pt-3 text-sm text-muted-foreground">
                <p className="m-0">
                  {q.data.note_id
                    ? `Committed to memory (note ${q.data.note_id.slice(0, 8)}…).`
                    : "Committed to memory."}
                  {q.data.indexed_at ? ` Committed ${formatDateTime(q.data.indexed_at, { withTime: false })}.` : ""}
                </p>
                <p className="m-0 mt-1 text-xs">
                  Find it through chat context or search. (A direct link from here arrives with search parity.)
                </p>
              </div>
            </>
          )}

          <div>
            <Button type="button" variant="outline" onClick={onClose}>
              Close
            </Button>
          </div>
        </CardContent>
      </Card>
    </section>
  );
}
