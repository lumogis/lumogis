// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Document library list page (LUM-160).

import { useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import { isShared, type DocumentSummary } from "../../api/documents";
import { EmptyState } from "../_shared/EmptyState";
import { ErrorState } from "../_shared/ErrorState";
import { LoadingPlaceholder, Skeleton } from "../_shared/Skeleton";
import { useDocuments, shareStatusLabel, statusLabel } from "./useDocuments";
import { useDocumentsSseInvalidation } from "./useDocumentsSseInvalidation";
import { DocumentUploadPanel } from "./DocumentUploadPanel";

// A document is "shared with the household" from the list's perspective when
// its share lifecycle is shared/partial (owner rows keep scope='personal' but
// carry share_status), or it is a member-visible shared projection row.
function docIsShared(doc: DocumentSummary): boolean {
  return isShared(doc.share_status) || doc.scope === "shared";
}

function shareBadge(doc: DocumentSummary): string {
  if (doc.scope === "system") return "System";
  if (doc.share_status && doc.share_status !== "personal") {
    return shareStatusLabel(doc.share_status);
  }
  return doc.scope === "shared" ? "Shared" : "Personal";
}

const DOCUMENT_COLUMNS = ["Name", "Type", "Entities", "Scope", "Status", "Indexed"] as const;

export function DocumentsPage(): JSX.Element {
  const { client, tokens } = useAuth();
  const { data, isLoading, error, refetch } = useDocuments(client);
  const [sharedOnly, setSharedOnly] = useState(false);
  useDocumentsSseInvalidation(tokens);

  const allDocuments = data?.documents ?? [];
  const documents = sharedOnly
    ? allDocuments.filter(docIsShared)
    : allDocuments;

  if (isLoading) {
    return (
      <section className="lumogis-documents" data-testid="documents-page">
        <h1>Library</h1>
        <LoadingPlaceholder label="Loading documents…">
          <table className="lumogis-documents__table">
            <thead>
              <tr>
                {DOCUMENT_COLUMNS.map((c) => (
                  <th scope="col" key={c}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody data-testid="documents-skeleton">
              {Array.from({ length: 6 }, (_, r) => (
                <tr key={r}>
                  {DOCUMENT_COLUMNS.map((c, i) => (
                    <td key={c}>
                      <Skeleton width={i === 0 ? "70%" : "45%"} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </LoadingPlaceholder>
      </section>
    );
  }

  if (error) {
    return (
      <ErrorState
        title="Couldn't load your library"
        message="Lumogis couldn't load your documents. This is usually temporary."
        onRetry={() => void refetch()}
      />
    );
  }

  if (allDocuments.length === 0) {
    return (
      <section className="lumogis-documents" data-testid="documents-page">
        <h1>Library</h1>
        <DocumentUploadPanel client={client} />
        <EmptyState
        title="No documents yet"
        helperText="Upload a file via Capture or ingest to build your library."
        actions={[
          { label: "Open Capture", href: "/capture", primary: true },
        ]}
        className="lumogis-documents__empty"
        />
      </section>
    );
  }

  return (
    <section className="lumogis-documents" data-testid="documents-page">
      <h1>Library</h1>
      <DocumentUploadPanel client={client} />
      <label className="lumogis-documents__filter">
        <input
          type="checkbox"
          checked={sharedOnly}
          onChange={(e) => setSharedOnly(e.target.checked)}
          data-testid="documents-shared-filter"
        />
        Shared with household
      </label>
      {documents.length === 0 ? (
        <p data-testid="documents-shared-empty">
          No documents shared with your household yet.
        </p>
      ) : (
      <table className="lumogis-documents__table">
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Type</th>
            <th scope="col">Entities</th>
            <th scope="col">Scope</th>
            <th scope="col">Status</th>
            <th scope="col">Indexed</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => {
            const key = doc.document_id ?? `inflight-${doc.in_flight_job_id}`;
            const detailHref =
              doc.document_id !== null ? `/documents/${doc.document_id}` : undefined;
            return (
              <tr key={key} data-document-id={doc.document_id ?? undefined}>
                <td>
                  {detailHref ? (
                    <Link to={detailHref}>{doc.display_name}</Link>
                  ) : (
                    doc.display_name
                  )}
                </td>
                <td>{doc.file_type || "—"}</td>
                <td>{doc.entity_count}</td>
                <td>{shareBadge(doc)}</td>
                <td>
                  <span className={`lumogis-documents__status lumogis-documents__status--${doc.status}`}>
                    {statusLabel(doc.status)}
                  </span>
                </td>
                <td>{doc.indexed_at ? new Date(doc.indexed_at).toLocaleString() : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      )}
    </section>
  );
}
