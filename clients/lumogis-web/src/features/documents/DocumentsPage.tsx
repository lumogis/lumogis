// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Document library list page (LUM-160).

import { useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import { isShared, type DocumentSummary } from "../../api/documents";
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageHeader } from "../../components/page/PageHeader";
import { Badge } from "../../components/ui/badge";
import { Checkbox } from "../../components/ui/checkbox";
import { Label } from "../../components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { formatDateTime } from "../../util/formatTimestamp";
import { originLabel } from "./documentOrigin";
import { humanizeStoredName } from "../../util/humanizeStoredName";
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

const HEADER_PROPS = {
  title: "Library",
  description: "Your documents, what Lumogis found in them, and who can see them.",
};

// On a phone each row stacks: the name takes the full width, the rest wrap below.
const ROW = "max-sm:flex max-sm:flex-wrap max-sm:items-center max-sm:gap-x-3";

function statusVariant(status: DocumentSummary["status"]): "secondary" | "destructive" | "outline" {
  if (status === "failed") return "destructive";
  if (status === "indexing") return "outline";
  return "secondary";
}

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
      <section data-testid="documents-page">
        <PageHeader {...HEADER_PROPS} />
        <LoadingPlaceholder label="Loading documents…">
          <div data-testid="documents-skeleton">
            <SkeletonText lines={6} />
          </div>
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
      <section data-testid="documents-page">
        <PageHeader {...HEADER_PROPS} />
        <DocumentUploadPanel client={client} />
        <EmptyState
          title="No documents yet"
          helperText="Upload a file via Capture or ingest to build your library."
          actions={[
            { label: "Open Capture", href: "/capture", primary: true },
          ]}
        />
      </section>
    );
  }

  return (
    <section data-testid="documents-page">
      <PageHeader {...HEADER_PROPS} />
      <DocumentUploadPanel client={client} />
      <div className="mb-3 flex items-center gap-2">
        <Checkbox
          id="documents-shared-only"
          checked={sharedOnly}
          onCheckedChange={(checked) => setSharedOnly(checked === true)}
          data-testid="documents-shared-filter"
        />
        <Label htmlFor="documents-shared-only" className="font-normal">
          Shared with household
        </Label>
      </div>
      {documents.length === 0 ? (
        <p data-testid="documents-shared-empty" className="text-sm text-muted-foreground">
          No documents shared with your household yet.
        </p>
      ) : (
        <Table>
          <TableHeader className="max-sm:hidden">
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Entities</TableHead>
              <TableHead>Scope</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Indexed</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {documents.map((doc) => {
              const key = doc.document_id ?? `inflight-${doc.in_flight_job_id}`;
              const detailHref =
                doc.document_id !== null ? `/documents/${doc.document_id}` : undefined;
              const label = humanizeStoredName(doc.display_name, doc.file_path);
              return (
                <TableRow key={key} data-document-id={doc.document_id ?? undefined} className={ROW}>
                  <TableCell className="whitespace-normal font-medium max-sm:w-full">
                    {detailHref ? (
                      <Link to={detailHref} className="text-accent-text underline underline-offset-2">
                        {label}
                      </Link>
                    ) : (
                      label
                    )}
                    {/* LUM-724: the library now spans three stores. The badge is
                        what keeps one list honest — it answers "where do I go to
                        work with this?", which differs per origin. Uploads get no
                        badge: they are the unremarkable case, and badging every
                        row would make the badge worthless. */}
                    {originLabel(doc.file_path) ? (
                      <Badge variant="outline" className="ml-2" data-testid="document-origin">
                        {originLabel(doc.file_path)}
                      </Badge>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{doc.file_type || "—"}</TableCell>
                  <TableCell className="text-muted-foreground">
                    <span className="sm:hidden">Entities: </span>
                    {doc.entity_count}
                  </TableCell>
                  <TableCell>{shareBadge(doc)}</TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(doc.status)}>{statusLabel(doc.status)}</Badge>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {formatDateTime(doc.indexed_at)}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </section>
  );
}
