// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Multi-file library upload with batch counter (LUM-511 Phase B).

import { useCallback, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "../../api/client";
import { ApiError } from "../../api/client";
import { getIngestBatch, uploadIngestFile } from "../../api/ingest";
import { PageSection } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { cn } from "cn";
import { IngestProgressBar } from "./IngestProgressBar";
import { documentsQueryKey } from "./useDocuments";
import { useIngestJobProgress } from "./useIngestJobProgress";
import { randomUuid } from "../../util/uuid";

/** Align with orchestrator extractor extensions (common document types). */
export const INGEST_UPLOAD_ACCEPT =
  ".txt,.pdf,.md,.markdown,.html,.htm,.docx,.pptx,.xlsx,.csv,.json,.xml,.rst";

interface UploadRow {
  fileName: string;
  jobId: number;
  fileId: string;
}

const ingestBatchQueryKey = (batchId: string) => ["ingest-batch", batchId] as const;

function UploadRowProgress({
  client,
  row,
}: {
  client: ApiClient;
  row: UploadRow;
}): JSX.Element {
  const { data } = useIngestJobProgress(client, row.jobId);
  return (
    <li className="flex flex-col gap-1 py-2" data-job-id={row.jobId}>
      <span className="break-all text-sm font-medium">{row.fileName}</span>
      {data ? (
        <IngestProgressBar
          stage={data.stage}
          progressPct={data.progress_pct ?? 0}
          statusMessage={data.status_message}
        />
      ) : (
        <span className="text-xs text-muted-foreground">Queued…</span>
      )}
    </li>
  );
}

export function DocumentUploadPanel({ client }: { client: ApiClient }): JSX.Element {
  const qc = useQueryClient();
  // ui Input is a plain function component (no ref), so the hidden file input is
  // reached through its wrapper.
  const dropRef = useRef<HTMLDivElement | null>(null);
  const [rows, setRows] = useState<UploadRow[]>([]);
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [totalFiles, setTotalFiles] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const batchQuery = useQuery({
    queryKey: ingestBatchQueryKey(batchId ?? ""),
    queryFn: () => getIngestBatch(client, batchId!),
    enabled: batchId !== null,
    refetchInterval: (q) => {
      if (uploading) return 1000;
      const d = q.state.data;
      if (d && d.in_progress > 0) return 1000;
      return false;
    },
  });

  const onFilesSelected = useCallback(
    async (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return;
      const files = Array.from(fileList);
      const bid = randomUuid();
      setBatchId(bid);
      setTotalFiles(files.length);
      setSelectedNames(files.map((f) => f.name));
      setRows([]);
      setUploading(true);
      setError(null);

      try {
        for (const file of files) {
          try {
            const res = await uploadIngestFile(client, file, { batchId: bid });
            setRows((prev) => [
              ...prev,
              { fileName: file.name, jobId: res.job_id, fileId: res.file_id },
            ]);
          } catch (err) {
            const msg =
              err instanceof ApiError
                ? `${file.name}: ${err.detail}`
                : `${file.name}: upload failed`;
            setError((prev) => (prev ? `${prev}; ${msg}` : msg));
          }
        }
      } finally {
        setUploading(false);
        void qc.invalidateQueries({ queryKey: documentsQueryKey });
      }
    },
    [client, qc],
  );

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (uploading) return;
    void onFilesSelected(e.dataTransfer.files);
  };

  const batch = batchQuery.data;
  const finishedCount = batch ? batch.completed + batch.failed : 0;
  const showCounter = totalFiles > 0;
  const chipNames =
    rows.length > 0
      ? rows.map((r) => r.fileName)
      : selectedNames;

  return (
    <div data-testid="document-upload-panel">
      <PageSection title="Upload documents">
        <div
          ref={dropRef}
          className={cn(
            "flex flex-col items-center gap-3 rounded-lg border border-dashed p-6 text-center",
            dragOver ? "border-primary bg-muted" : "border-border",
          )}
          onDragEnter={(e) => {
            e.preventDefault();
            if (!uploading) setDragOver(true);
          }}
          onDragOver={(e) => {
            e.preventDefault();
            if (!uploading) setDragOver(true);
          }}
          onDragLeave={(e) => {
            e.preventDefault();
            if (e.currentTarget === e.target) setDragOver(false);
          }}
          onDrop={onDrop}
        >
          <Input
            type="file"
            multiple
            accept={INGEST_UPLOAD_ACCEPT}
            disabled={uploading}
            aria-label="Choose files to upload"
            tabIndex={-1}
            className="sr-only"
            onChange={(e) => {
              void onFilesSelected(e.target.files);
              e.target.value = "";
            }}
          />
          <p className="m-0 text-sm text-muted-foreground">
            Drop files here or choose from your device.
          </p>
          <Button
            type="button"
            variant="secondary"
            disabled={uploading}
            onClick={() => dropRef.current?.querySelector("input")?.click()}
          >
            Choose files
          </Button>
        </div>
        {chipNames.length > 0 ? (
          <ul className="m-0 mt-3 flex list-none flex-wrap gap-2 p-0" aria-label="Selected files">
            {chipNames.map((name) => (
              <li key={name}>
                <Badge variant="secondary" className="h-auto whitespace-normal break-all">
                  {name}
                </Badge>
              </li>
            ))}
          </ul>
        ) : null}
        {showCounter ? (
          <p className="m-0 mt-3 text-sm text-muted-foreground" data-testid="ingest-batch-counter">
            {finishedCount} of {totalFiles}
          </p>
        ) : null}
        {error ? (
          <Alert variant="destructive" className="mt-3">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        {rows.length > 0 ? (
          <ul className="m-0 mt-3 list-none divide-y divide-border p-0">
            {rows.map((row) => (
              <UploadRowProgress key={row.jobId} client={client} row={row} />
            ))}
          </ul>
        ) : null}
      </PageSection>
    </div>
  );
}
