// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { DocumentDetailView } from "../../../src/features/documents/DocumentDetailView";

const deleteMutate = vi.fn();
const reingestMutate = vi.fn();
let deleteMutationIsPending = false;

vi.mock("../../../src/auth/AuthProvider", () => ({
  useAuth: () => ({
    client: {},
    tokens: { get: () => null, set: () => {}, clear: () => {} },
  }),
}));

vi.mock("../../../src/features/documents/useDocumentsSseInvalidation", () => ({
  useDocumentsSseInvalidation: () => {},
}));

const shareMutate = vi.fn();
const unshareMutate = vi.fn();

vi.mock("../../../src/features/documents/useDocuments", () => ({
  useDocument: vi.fn(),
  useDeleteDocument: () => ({ mutateAsync: deleteMutate, isPending: deleteMutationIsPending }),
  useReingestDocument: () => ({ mutateAsync: reingestMutate }),
  useShareDocument: () => ({ mutateAsync: shareMutate, isPending: false }),
  useUnshareDocument: () => ({ mutateAsync: unshareMutate, isPending: false }),
  shareStatusLabel: (s: string | undefined) => s ?? "Personal",
  statusLabel: (s: string) => s,
}));

import { useDocument } from "../../../src/features/documents/useDocuments";

const BASE_DOC = {
  document_id: 5,
  display_name: "notes.txt",
  file_path: "/uploads/u/notes.txt",
  file_type: ".txt",
  chunk_count: 1,
  entity_count: 0,
  scope: "personal" as const,
  status: "indexed" as const,
  indexed_at: null,
  error_message: null,
  file_hash: "h",
  entities: [],
  source_available: true,
};

// Delete is last in the "…" menu and confirmed in an AlertDialog.
async function deleteViaMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "More actions for notes.txt" }));
  await user.click(await screen.findByRole("menuitem", { name: "Delete document" }));
  const dialog = await screen.findByRole("alertdialog");
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));
}

function renderDetail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/documents/5"]}>
        <Routes>
          <Route path="/documents/:documentId" element={<DocumentDetailView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DocumentDetailView", () => {
  beforeEach(() => {
    deleteMutate.mockReset();
    reingestMutate.mockReset();
    shareMutate.mockReset();
    unshareMutate.mockReset();
    deleteMutationIsPending = false;
    vi.mocked(useDocument).mockReturnValue({
      data: BASE_DOC,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useDocument>);
  });

  it("shows delete confirm and calls delete mutation", async () => {
    deleteMutate.mockResolvedValue({ deleted: true, partial: false, document_id: 5, errors: [] });

    const user = userEvent.setup();
    renderDetail();

    await deleteViaMenu(user);
    expect(deleteMutate).toHaveBeenCalledWith(5);
  });

  it("Cancel in the delete confirm does nothing", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "More actions for notes.txt" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete document" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("deleted permanently");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(deleteMutate).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  });

  it("Delete is the last item in the menu and 'Force re-ingest' is now 'Read again'", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "More actions for notes.txt" }));
    const items = (await screen.findAllByRole("menuitem")).map((el) => el.textContent);
    expect(items).toEqual(["Check for changes", "Read again", "Delete document"]);
    expect(screen.queryByText(/force re-ingest/i)).toBeNull();
  });

  it("'Read again' forces a re-ingest; 'Check for changes' does not", async () => {
    reingestMutate.mockResolvedValue({ job_id: 9 });
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "More actions for notes.txt" }));
    await user.click(await screen.findByRole("menuitem", { name: "Read again" }));
    expect(reingestMutate).toHaveBeenCalledWith({ documentId: 5, force: true });

    await user.click(screen.getByRole("button", { name: "More actions for notes.txt" }));
    await user.click(await screen.findByRole("menuitem", { name: "Check for changes" }));
    expect(reingestMutate).toHaveBeenLastCalledWith({ documentId: 5, force: false });
  });

  it("keeps the raw file path in a collapsed Details", async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("heading", { name: "notes.txt" });
    expect(screen.queryByText(/\/uploads\/u\/notes\.txt/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(await screen.findByTitle("#5 · /uploads/u/notes.txt")).toBeTruthy();
  });

  it("explains instead of offering a re-read when the source file is gone", async () => {
    vi.mocked(useDocument).mockReturnValue({
      data: { ...BASE_DOC, source_available: false },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useDocument>);
    const user = userEvent.setup();
    renderDetail();

    expect(await screen.findByText(/no longer on disk/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "More actions for notes.txt" }));
    const items = (await screen.findAllByRole("menuitem")).map((el) => el.textContent);
    expect(items).toEqual(["Delete document"]);
  });

  it("shows an error state when the document is not found", async () => {
    vi.mocked(useDocument).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("nope"),
    } as unknown as ReturnType<typeof useDocument>);
    renderDetail();
    expect(await screen.findByText("Document not found")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to library" })).toBeTruthy();
  });

  it("shows failed status and error message", async () => {
    vi.mocked(useDocument).mockReturnValue({
      data: { ...BASE_DOC, status: "failed", error_message: "Ingest failed", source_available: false },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useDocument>);

    renderDetail();
    expect(await screen.findByText("failed")).toBeTruthy();
    expect(screen.getByText("Ingest failed")).toBeTruthy();
  });

  // LUM-500 partial-failure UX tests

  it("does not navigate away on partial delete — shows banner and retry button", async () => {
    deleteMutate.mockResolvedValue({
      deleted: true,
      partial: true,
      document_id: 5,
      errors: ["qdrant: attempt 3/3 failed: connection refused"],
    });

    const user = userEvent.setup();
    renderDetail();
    await deleteViaMenu(user);

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry cleanup" })).toBeTruthy();
    // Page should NOT have navigated — document heading still visible.
    expect(screen.getByText("notes.txt")).toBeTruthy();
  });

  it("shows qdrant-specific error message on qdrant arm failure", async () => {
    deleteMutate.mockResolvedValue({
      deleted: true,
      partial: true,
      document_id: 5,
      errors: ["qdrant: attempt 3/3 failed: timeout"],
    });

    const user = userEvent.setup();
    renderDetail();
    await deleteViaMenu(user);

    expect(
      await screen.findByText("Search index copies of this document may still exist."),
    ).toBeTruthy();
  });

  it("shows graph-specific error message on graph arm failure", async () => {
    deleteMutate.mockResolvedValue({
      deleted: true,
      partial: true,
      document_id: 5,
      errors: ["graph: attempt 3/3 failed: falkordb unreachable"],
    });

    const user = userEvent.setup();
    renderDetail();
    await deleteViaMenu(user);

    expect(
      await screen.findByText("Knowledge graph entries for this document may still exist."),
    ).toBeTruthy();
  });

  it("navigates away after successful retry", async () => {
    deleteMutate
      .mockResolvedValueOnce({
        deleted: true,
        partial: true,
        document_id: 5,
        errors: ["qdrant: attempt 3/3 failed: timeout"],
      })
      .mockResolvedValueOnce({ deleted: true, partial: false, document_id: 5, errors: [] });

    const user = userEvent.setup();
    renderDetail();
    await deleteViaMenu(user);
    await screen.findByRole("button", { name: "Retry cleanup" });

    await user.click(screen.getByRole("button", { name: "Retry cleanup" }));
    // After successful retry the component navigates; heading unmounts.
    expect(screen.queryByText("notes.txt")).toBeNull();
  });

  it("renders owner share toggle on the detail view (LUM-157)", async () => {
    vi.mocked(useDocument).mockReturnValue({
      data: { ...BASE_DOC, share_status: "personal", is_owner: true },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useDocument>);

    renderDetail();
    expect(await screen.findByTestId("share-toggle")).toBeTruthy();
  });

  it("renders read-only share indicator for a non-owner (LUM-157)", async () => {
    vi.mocked(useDocument).mockReturnValue({
      data: {
        ...BASE_DOC,
        scope: "shared",
        share_status: "shared",
        is_owner: false,
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useDocument>);

    renderDetail();
    expect(await screen.findByTestId("share-indicator")).toBeTruthy();
    expect(screen.queryByTestId("share-toggle")).toBeNull();
  });

  it("shows escalation copy when retry also returns partial", async () => {
    deleteMutate.mockResolvedValue({
      deleted: true,
      partial: true,
      document_id: 5,
      errors: ["qdrant: attempt 3/3 failed: timeout"],
    });

    const user = userEvent.setup();
    renderDetail();
    await deleteViaMenu(user);
    await screen.findByRole("button", { name: "Retry cleanup" });
    await user.click(screen.getByRole("button", { name: "Retry cleanup" }));

    expect(
      await screen.findByText(/contact your administrator/i),
    ).toBeTruthy();
    expect(screen.getByText(/reference: document #5/i)).toBeTruthy();
  });
});
