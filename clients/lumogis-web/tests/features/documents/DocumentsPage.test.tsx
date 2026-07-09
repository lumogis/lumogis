// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { type ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { DocumentsPage } from "../../../src/features/documents/DocumentsPage";

function wrapper(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

vi.mock("../../../src/auth/AuthProvider", () => ({
  useAuth: () => ({
    client: {},
    tokens: { get: () => null, set: () => {}, clear: () => {} },
  }),
}));

vi.mock("../../../src/features/documents/useDocuments", () => ({
  useDocuments: vi.fn(),
  statusLabel: (s: string) => s,
  shareStatusLabel: (s: string | undefined) => {
    if (s === "shared") return "Shared";
    if (s === "sharing") return "Sharing…";
    return "Personal";
  },
}));

vi.mock("../../../src/features/documents/useDocumentsSseInvalidation", () => ({
  useDocumentsSseInvalidation: () => {},
}));

import { useDocuments } from "../../../src/features/documents/useDocuments";

describe("DocumentsPage", () => {
  it("renders empty state when API returns no documents", async () => {
    vi.mocked(useDocuments).mockReturnValue({
      data: { documents: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useDocuments>);

    render(wrapper(<DocumentsPage />));
    expect(await screen.findByText("No documents yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Capture" })).toBeTruthy();
  });

  it("renders status pills for indexed documents", async () => {
    vi.mocked(useDocuments).mockReturnValue({
      data: {
        documents: [
          {
            document_id: 1,
            display_name: "report.pdf",
            file_path: "/uploads/u/report.pdf",
            file_type: ".pdf",
            chunk_count: 2,
            entity_count: 0,
            scope: "personal",
            status: "indexed",
            indexed_at: "2026-06-01T12:00:00Z",
            error_message: null,
          },
        ],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useDocuments>);

    render(wrapper(<DocumentsPage />));
    expect(await screen.findByText("report.pdf")).toBeTruthy();
    expect(screen.getByText("indexed")).toBeTruthy();
  });

  it("shows a Shared badge for an owner's shared document (LUM-157)", async () => {
    vi.mocked(useDocuments).mockReturnValue({
      data: {
        documents: [
          {
            document_id: 1,
            display_name: "shared.pdf",
            file_path: "/uploads/u/shared.pdf",
            file_type: ".pdf",
            chunk_count: 2,
            entity_count: 0,
            scope: "personal",
            status: "indexed",
            indexed_at: "2026-06-01T12:00:00Z",
            error_message: null,
            share_status: "shared",
            is_owner: true,
          },
        ],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useDocuments>);

    render(wrapper(<DocumentsPage />));
    expect(await screen.findByText("Shared")).toBeTruthy();
  });

  it("filters to shared documents when the household filter is checked (LUM-157)", async () => {
    const user = userEvent.setup();
    vi.mocked(useDocuments).mockReturnValue({
      data: {
        documents: [
          {
            document_id: 1,
            display_name: "private.pdf",
            file_path: "/uploads/u/private.pdf",
            file_type: ".pdf",
            chunk_count: 1,
            entity_count: 0,
            scope: "personal",
            status: "indexed",
            indexed_at: null,
            error_message: null,
            share_status: "personal",
            is_owner: true,
          },
          {
            document_id: 2,
            display_name: "shared.pdf",
            file_path: "/uploads/u/shared.pdf",
            file_type: ".pdf",
            chunk_count: 1,
            entity_count: 0,
            scope: "personal",
            status: "indexed",
            indexed_at: null,
            error_message: null,
            share_status: "shared",
            is_owner: true,
          },
        ],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useDocuments>);

    render(wrapper(<DocumentsPage />));
    expect(await screen.findByText("private.pdf")).toBeTruthy();
    expect(screen.getByText("shared.pdf")).toBeTruthy();

    await user.click(screen.getByTestId("documents-shared-filter"));
    expect(screen.queryByText("private.pdf")).toBeNull();
    expect(screen.getByText("shared.pdf")).toBeTruthy();
  });
});
