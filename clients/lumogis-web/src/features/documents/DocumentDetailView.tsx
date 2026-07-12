// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Per-document detail view (LUM-160 / LUM-500).

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import { MetadataCaption } from "../../components/MetadataCaption";
import { Button } from "../../components/Button";
import {
  documentMetadataCaption,
  humanizeStoredName,
} from "../../util/humanizeStoredName";
import {
  useDeleteDocument,
  useDocument,
  useReingestDocument,
  statusLabel,
} from "./useDocuments";
import { useDocumentsSseInvalidation } from "./useDocumentsSseInvalidation";
import { IngestProgressBar } from "./IngestProgressBar";
import { useIngestJobProgress } from "./useIngestJobProgress";
import { ShareToggle } from "./ShareToggle";

function parseDocumentId(raw: string | undefined): number | null {
  if (!raw) return null;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function categorizeDeleteErrors(errors: string[]): string[] {
  const msgs: string[] = [];
  if (errors.some((e) => e.startsWith("qdrant:")))
    msgs.push("Search index copies of this document may still exist.");
  if (errors.some((e) => e.startsWith("graph:")))
    msgs.push("Knowledge graph entries for this document may still exist.");
  if (msgs.length === 0) msgs.push("Partial deletion — some copies may remain.");
  return msgs;
}

export function DocumentDetailView(): JSX.Element {
  const { documentId: rawId } = useParams<{ documentId: string }>();
  const documentId = parseDocumentId(rawId);
  const navigate = useNavigate();
  const { client, tokens } = useAuth();
  const { data: doc, isLoading, error } = useDocument(client, documentId);
  const deleteMutation = useDeleteDocument(client);
  const reingestMutation = useReingestDocument(client);
  const [deleteErrors, setDeleteErrors] = useState<string[] | null>(null);

  useDocumentsSseInvalidation(tokens);

  const activeJobId =
    reingestMutation.data?.job_id ??
    (doc?.status === "indexing" ? doc.in_flight_job_id ?? null : null);
  const { data: ingestProgress } = useIngestJobProgress(client, activeJobId);

  const shareJobId = doc?.in_flight_share_job_id ?? null;
  const { data: shareProgress } = useIngestJobProgress(client, shareJobId);

  if (documentId === null) {
    return <p role="alert">Invalid document id.</p>;
  }

  if (isLoading) {
    return <p>Loading document…</p>;
  }

  if (error || !doc) {
    return (
      <p role="alert">
        Document not found.{" "}
        <Link to="/documents">Back to library</Link>
      </p>
    );
  }

  const handleDelete = async () => {
    if (!window.confirm(`Delete "${doc.display_name}" permanently?`)) {
      return;
    }
    const res = await deleteMutation.mutateAsync(documentId);
    if (!res.partial) {
      navigate("/documents");
      return;
    }
    setDeleteErrors(res.errors);
  };

  const handleRetry = async () => {
    const res = await deleteMutation.mutateAsync(documentId);
    if (!res.partial) {
      navigate("/documents");
      return;
    }
    setDeleteErrors(res.errors);
  };

  const handleReingest = async (force: boolean) => {
    await reingestMutation.mutateAsync({ documentId, force });
  };

  const title = humanizeStoredName(doc.display_name, doc.file_path);

  return (
    <section className="lumogis-document-detail" data-testid="document-detail">
      <p>
        <Link to="/documents">← Library</Link>
      </p>
      <h1>{title}</h1>
      <MetadataCaption
        value={documentMetadataCaption(documentId, doc.display_name, doc.file_path)}
        label="Copy id"
      />
      {deleteErrors !== null && (
        <div role="alert" className="lumogis-document-detail__partial-alert">
          <p>Deletion incomplete:</p>
          <ul>
            {categorizeDeleteErrors(deleteErrors).map((msg) => (
              <li key={msg}>{msg}</li>
            ))}
          </ul>
          {deleteMutation.isPending ? (
            <p>Retrying…</p>
          ) : (
            <>
              <Button type="button" variant="secondary" size="sm" onClick={() => void handleRetry()}>
                Retry cleanup
              </Button>
              <p className="lumogis-document-detail__partial-escalation">
                If this persists after retrying, contact your administrator.
                Reference: document #{documentId}.
              </p>
            </>
          )}
        </div>
      )}
      <dl className="lumogis-document-detail__meta lumogis-kv-list">
        <div className="lumogis-kv-row">
          <dt className="lumogis-kv-row__label">Status</dt>
          <dd className="lumogis-kv-row__value">
            <span className={`lumogis-documents__status lumogis-documents__status--${doc.status}`}>
              {statusLabel(doc.status)}
            </span>
          </dd>
        </div>
        {doc.status === "indexing" && activeJobId && ingestProgress ? (
          <div className="lumogis-kv-row">
            <dt className="lumogis-kv-row__label">Progress</dt>
            <dd className="lumogis-kv-row__value">
              <IngestProgressBar
                stage={ingestProgress.stage}
                progressPct={ingestProgress.progress_pct ?? 0}
                statusMessage={ingestProgress.status_message}
              />
            </dd>
          </div>
        ) : null}
        <div className="lumogis-kv-row">
          <dt className="lumogis-kv-row__label">Sharing</dt>
          <dd className="lumogis-kv-row__value">
            <ShareToggle
              client={client}
              documentId={documentId}
              displayName={title}
              shareStatus={doc.share_status}
              isOwner={doc.is_owner ?? true}
              sharedBy={doc.shared_by}
            />
            {shareJobId && shareProgress ? (
              <IngestProgressBar
                stage={shareProgress.stage}
                progressPct={shareProgress.progress_pct ?? 0}
                statusMessage={shareProgress.status_message}
              />
            ) : null}
          </dd>
        </div>
        <div className="lumogis-kv-row">
          <dt className="lumogis-kv-row__label">Chunks</dt>
          <dd className="lumogis-kv-row__value">{doc.chunk_count}</dd>
        </div>
        <div className="lumogis-kv-row">
          <dt className="lumogis-kv-row__label">Entities</dt>
          <dd className="lumogis-kv-row__value">{doc.entity_count}</dd>
        </div>
        {doc.error_message ? (
          <div className="lumogis-kv-row">
            <dt className="lumogis-kv-row__label">Error</dt>
            <dd className="lumogis-kv-row__value">{doc.error_message}</dd>
          </div>
        ) : null}
      </dl>
      {doc.entities.length > 0 ? (
        <section>
          <h2>Linked entities</h2>
          <ul>
            {doc.entities.map((e) => (
              <li key={e.entity_id}>
                <Link to={`/entities/${encodeURIComponent(e.entity_id)}`}>
                  {e.name}
                </Link>{" "}
                <span className="lumogis-document-detail__entity-type">({e.entity_type})</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <div className="lumogis-document-detail__actions lumogis-form-actions">
        {doc.scope === "personal" && deleteErrors === null ? (
          <Button type="button" variant="danger-solid" onClick={() => void handleDelete()}>
            Delete document
          </Button>
        ) : null}
        {doc.source_available ? (
          <>
            <Button type="button" variant="secondary" onClick={() => void handleReingest(false)}>
              Re-ingest
            </Button>
            <Button type="button" variant="secondary" onClick={() => void handleReingest(true)}>
              Force re-ingest
            </Button>
          </>
        ) : (
          <p>Source file is no longer on disk — re-upload via Capture to index again.</p>
        )}
      </div>
    </section>
  );
}
