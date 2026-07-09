// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// React Query hooks for document library (LUM-160).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "../../api/client";
import {
  deleteDocument,
  getDocument,
  listDocuments,
  publishDocument,
  reingestDocument,
  unpublishDocument,
  type DocumentDetail,
  type DocumentSummary,
  type ShareStatus,
} from "../../api/documents";

export const documentsQueryKey = ["documents"] as const;

// A share is mid-flight (poll for progress) while sharing/unsharing.
function isShareTransient(status: ShareStatus | undefined): boolean {
  return status === "sharing" || status === "unsharing";
}

export function useDocuments(client: ApiClient) {
  return useQuery({
    queryKey: documentsQueryKey,
    queryFn: () => listDocuments(client),
    refetchInterval: (q) => {
      const docs = q.state.data?.documents ?? [];
      const busy = docs.some(
        (d) => d.status === "indexing" || isShareTransient(d.share_status),
      );
      return busy ? 2000 : false;
    },
  });
}

export function useDocument(client: ApiClient, documentId: number | null) {
  return useQuery({
    queryKey: [...documentsQueryKey, documentId] as const,
    queryFn: () => getDocument(client, documentId!),
    enabled: documentId !== null && documentId > 0,
    refetchInterval: (q) => {
      const d = q.state.data;
      return d?.status === "indexing" || isShareTransient(d?.share_status)
        ? 2000
        : false;
    },
  });
}

export function useDeleteDocument(client: ApiClient) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (documentId: number) => deleteDocument(client, documentId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: documentsQueryKey });
    },
  });
}

export function useReingestDocument(client: ApiClient) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ documentId, force }: { documentId: number; force?: boolean }) =>
      reingestDocument(client, documentId, { force }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: documentsQueryKey });
    },
  });
}

export function useShareDocument(client: ApiClient) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (documentId: number) => publishDocument(client, documentId),
    onSuccess: (_res, documentId) => {
      void qc.invalidateQueries({ queryKey: documentsQueryKey });
      void qc.invalidateQueries({
        queryKey: [...documentsQueryKey, documentId] as const,
      });
    },
  });
}

export function useUnshareDocument(client: ApiClient) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (documentId: number) => unpublishDocument(client, documentId),
    onSuccess: (_res, documentId) => {
      void qc.invalidateQueries({ queryKey: documentsQueryKey });
      void qc.invalidateQueries({
        queryKey: [...documentsQueryKey, documentId] as const,
      });
    },
  });
}

export function shareStatusLabel(status: ShareStatus | undefined): string {
  switch (status) {
    case "sharing":
      return "Sharing…";
    case "shared":
      return "Shared";
    case "unsharing":
      return "Unsharing…";
    case "partial":
      return "Shared (finishing)";
    case "personal":
    default:
      return "Personal";
  }
}

export function statusLabel(status: DocumentSummary["status"]): string {
  switch (status) {
    case "indexing":
      return "Indexing";
    case "indexed":
      return "Indexed";
    case "failed":
      return "Failed";
    default:
      return status;
  }
}

export type { DocumentDetail, DocumentSummary };
