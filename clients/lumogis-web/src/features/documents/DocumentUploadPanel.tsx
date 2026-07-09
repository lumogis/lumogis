// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Multi-file library upload with batch counter (LUM-511 Phase B).

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "../../api/client";
import { ApiError } from "../../api/client";
import { getIngestBatch, uploadIngestFile } from "../../api/ingest";
import { IngestProgressBar } from "./IngestProgressBar";
import { documentsQueryKey } from "./useDocuments";
import { useIngestJobProgress } from "./useIngestJobProgress";

/** Align with orchestrator extractor extensions (common document types). */
export const INGEST_UPLOAD_ACCEPT =
  ".txt,.pdf,.md,.markdown,.html,.htm,.docx,.pptx,.xlsx,.csv,.json,.xml,.rst";

interface UploadRow {
  fileName: string;
  jobId: number;
  fileId: string;
}

// Module-local query-key factory (no external importer). If another module needs
// to invalidate this cache later, promote it to a shared queryKeys module.
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
    <li className="lumogis-ingest-upload__row" data-job-id={row.jobId}>
      <span className="lumogis-ingest-upload__file-name">{row.fileName}</span>
      {data ? (
        <IngestProgressBar
          stage={data.stage}
          progressPct={data.progress_pct ?? 0}
          statusMessage={data.status_message}
        />
      ) : (
        <span>Queued…</span>
      )}
    </li>
  );
}

export function DocumentUploadPanel({ client }: { client: ApiClient }): JSX.Element {
  const qc = useQueryClient();
  const [rows, setRows] = useState<UploadRow[]>([]);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [totalFiles, setTotalFiles] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const onFilesSelected = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);
    const bid = crypto.randomUUID();
    setBatchId(bid);
    setTotalFiles(files.length);
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
  };

  const batch = batchQuery.data;
  const finishedCount = batch ? batch.completed + batch.failed : 0;
  const showCounter = totalFiles > 0;

  return (
    <section className="lumogis-ingest-upload" data-testid="document-upload-panel">
      <h2>Upload documents</h2>
      <label className="lumogis-ingest-upload__picker">
        <span className="lumogis-ingest-upload__picker-label">Choose files</span>
        <input
          type="file"
          multiple
          accept={INGEST_UPLOAD_ACCEPT}
          disabled={uploading}
          onChange={(e) => {
            void onFilesSelected(e.target.files);
            e.target.value = "";
          }}
        />
      </label>
      {showCounter ? (
        <p className="lumogis-ingest-upload__counter" data-testid="ingest-batch-counter">
          {finishedCount} of {totalFiles}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="lumogis-ingest-upload__error">
          {error}
        </p>
      ) : null}
      {rows.length > 0 ? (
        <ul className="lumogis-ingest-upload__list">
          {rows.map((row) => (
            <UploadRowProgress key={row.jobId} client={client} row={row} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}
