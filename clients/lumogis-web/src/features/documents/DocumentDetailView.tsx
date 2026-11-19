// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Per-document detail view (LUM-160 / LUM-500).

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChevronDown } from "lucide-react";

import { useAuth } from "../../auth/AuthProvider";
import { MetadataCaption } from "../../components/MetadataCaption";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageHeader } from "../../components/page/PageHeader";
import { PageSection, Row } from "../../components/page/PageSection";
import { RowActions, type RowAction } from "../../components/page/RowActions";
import { Alert, AlertDescription } from "../../components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import {
  documentMetadataCaption,
  humanizeStoredName,
} from "../../util/humanizeStoredName";
import { formatDateTime } from "../../util/formatTimestamp";
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
import {
  EXTERNAL_REINGEST_EXPLANATION,
  EXTERNAL_SHARE_EXPLANATION,
  isExternal,
  originOf,
} from "./documentOrigin";

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
  const [confirmDelete, setConfirmDelete] = useState(false);

  useDocumentsSseInvalidation(tokens);

  const activeJobId =
    reingestMutation.data?.job_id ??
    (doc?.status === "indexing" ? doc.in_flight_job_id ?? null : null);
  const { data: ingestProgress } = useIngestJobProgress(client, activeJobId);

  const shareJobId = doc?.in_flight_share_job_id ?? null;
  const { data: shareProgress } = useIngestJobProgress(client, shareJobId);

  if (documentId === null) {
    return (
      <Alert variant="destructive">
        <AlertDescription>Invalid document id.</AlertDescription>
      </Alert>
    );
  }

  if (isLoading) {
    return (
      <LoadingPlaceholder label="Loading document…" className="max-w-2xl">
        <SkeletonText lines={6} />
      </LoadingPlaceholder>
    );
  }

  if (error || !doc) {
    return (
      <ErrorState
        title="Document not found"
        message="Lumogis couldn't find this document. It may have been deleted."
        actions={[{ label: "Back to library", href: "/documents" }]}
        doctorHint={false}
      />
    );
  }

  const runDelete = async () => {
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
  const external = isExternal(doc.file_path);

  // Actions beyond the page itself live in "…". Reading again needs the file on
  // disk (an external document is read by its own system); Delete is last.
  const actions: RowAction[] = [];
  if (!external && doc.source_available) {
    actions.push(
      { label: "Check for changes", onSelect: () => void handleReingest(false) },
      { label: "Read again", onSelect: () => void handleReingest(true) },
    );
  }
  if (doc.scope === "personal" && deleteErrors === null) {
    actions.push({
      label: "Delete document",
      destructive: true,
      onSelect: () => setConfirmDelete(true),
    });
  }

  return (
    <section data-testid="document-detail">
      <p className="mb-2">
        <Link to="/documents" className="text-accent-text underline underline-offset-2">
          ← Library
        </Link>
      </p>
      <PageHeader
        title={title}
        action={actions.length > 0 ? <RowActions label={`More actions for ${title}`} items={actions} /> : undefined}
      />
      {deleteErrors !== null && (
        <Alert variant="destructive" className="mb-4 max-w-2xl">
          <AlertDescription>
            <p className="m-0">Deletion incomplete:</p>
            <ul className="m-0 pl-5">
              {categorizeDeleteErrors(deleteErrors).map((msg) => (
                <li key={msg}>{msg}</li>
              ))}
            </ul>
            {deleteMutation.isPending ? (
              <p className="m-0 mt-2">Retrying…</p>
            ) : (
              <>
                <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => void runDelete()}>
                  Retry cleanup
                </Button>
                <p className="m-0 mt-2">
                  If this persists after retrying, contact your administrator.
                  Reference: document #{documentId}.
                </p>
              </>
            )}
          </AlertDescription>
        </Alert>
      )}
      <PageSection title="About this document">
        <dl className="m-0">
          <Row label="Status">
            <Badge variant={doc.status === "failed" ? "destructive" : "secondary"}>
              {statusLabel(doc.status)}
            </Badge>
          </Row>
          {doc.status === "indexing" && activeJobId && ingestProgress ? (
            <Row label="Progress">
              <IngestProgressBar
                stage={ingestProgress.stage}
                progressPct={ingestProgress.progress_pct ?? 0}
                statusMessage={ingestProgress.status_message}
              />
            </Row>
          ) : null}
          <Row label="Sharing">
            {/* LUM-724: the server refuses with 409, but a control that fails
                on click is worse than one that explains first — the reason is
                not something a user could guess. */}
            {external ? (
              <span data-testid="share-unavailable">{EXTERNAL_SHARE_EXPLANATION}</span>
            ) : (
              <ShareToggle
                client={client}
                documentId={documentId}
                displayName={title}
                shareStatus={doc.share_status}
                isOwner={doc.is_owner ?? true}
                sharedBy={doc.shared_by}
              />
            )}
            {shareJobId && shareProgress ? (
              <IngestProgressBar
                stage={shareProgress.stage}
                progressPct={shareProgress.progress_pct ?? 0}
                statusMessage={shareProgress.status_message}
              />
            ) : null}
          </Row>
          <Row label="Indexed">{formatDateTime(doc.indexed_at)}</Row>
          <Row label="Passages">{doc.chunk_count}</Row>
          <Row label="Entities">{doc.entity_count}</Row>
        </dl>
        {doc.error_message ? (
          <Alert variant="destructive" className="mt-3">
            <AlertDescription>{doc.error_message}</AlertDescription>
          </Alert>
        ) : null}
        {external ? (
          // Re-ingest reads the file from disk, which a logical path does not
          // name. The server already refuses; "source unavailable" would read as
          // a bug, so the origin is named instead.
          <p className="m-0 mt-3 text-sm text-muted-foreground" data-testid="reingest-unavailable">
            {EXTERNAL_REINGEST_EXPLANATION[originOf(doc.file_path) as "paperless" | "email"]}
          </p>
        ) : !doc.source_available ? (
          <p className="m-0 mt-3 text-sm text-muted-foreground">
            Source file is no longer on disk — re-upload via Capture to index again.
          </p>
        ) : null}
      </PageSection>
      {doc.entities.length > 0 ? (
        <PageSection title="Linked entities">
          <ul className="m-0 list-none p-0">
            {doc.entities.map((e) => (
              <li key={e.entity_id} className="py-1 text-sm">
                <Link
                  to={`/entities/${encodeURIComponent(e.entity_id)}`}
                  className="text-accent-text underline underline-offset-2"
                >
                  {e.name}
                </Link>{" "}
                <span className="text-muted-foreground">({e.entity_type})</span>
              </li>
            ))}
          </ul>
        </PageSection>
      ) : null}
      <Collapsible className="max-w-2xl">
        <CollapsibleTrigger
          render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
        >
          <ChevronDown aria-hidden="true" className="transition-transform group-aria-expanded/trigger:rotate-180" />
          Details
        </CollapsibleTrigger>
        <CollapsibleContent className="flex flex-col gap-2 pt-2 text-sm">
          <p className="m-0 text-muted-foreground">Where Lumogis keeps this file, for whoever runs it.</p>
          <MetadataCaption
            value={documentMetadataCaption(documentId, doc.display_name, doc.file_path)}
            label="Copy id"
          />
        </CollapsibleContent>
      </Collapsible>
      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this document?</AlertDialogTitle>
            <AlertDialogDescription>
              {`"${doc.display_name}" will be deleted permanently, with its search index and the knowledge it added. This can't be undone.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructiveSolid"
              onClick={() => {
                setConfirmDelete(false);
                void runDelete();
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
