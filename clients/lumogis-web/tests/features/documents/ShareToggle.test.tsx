// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { ShareToggle } from "../../../src/features/documents/ShareToggle";

const shareMutate = vi.fn();
const unshareMutate = vi.fn();

vi.mock("../../../src/features/documents/useDocuments", async () => {
  const actual = await vi.importActual<
    typeof import("../../../src/features/documents/useDocuments")
  >("../../../src/features/documents/useDocuments");
  return {
    ...actual,
    useShareDocument: () => ({ mutateAsync: shareMutate, isPending: false }),
    useUnshareDocument: () => ({ mutateAsync: unshareMutate, isPending: false }),
  };
});

const CLIENT = {} as never;

function renderToggle(overrides: Partial<Parameters<typeof ShareToggle>[0]> = {}) {
  return render(
    <ShareToggle
      client={CLIENT}
      documentId={5}
      displayName="notes.txt"
      shareStatus="personal"
      isOwner
      {...overrides}
    />,
  );
}

describe("ShareToggle", () => {
  beforeEach(() => {
    shareMutate.mockReset().mockResolvedValue({
      document_id: 5,
      job_id: 1,
      share_status: "sharing",
    });
    unshareMutate.mockReset().mockResolvedValue({
      document_id: 5,
      job_id: 2,
      share_status: "unsharing",
    });
    vi.spyOn(globalThis, "confirm").mockReturnValue(true);
  });

  it("owner + personal → unchecked switch; enabling confirms + publishes", async () => {
    const user = userEvent.setup();
    renderToggle({ shareStatus: "personal" });
    const sw = screen.getByRole("switch");
    expect((sw as HTMLInputElement).checked).toBe(false);

    await user.click(sw);
    expect(globalThis.confirm).toHaveBeenCalled();
    expect(shareMutate).toHaveBeenCalledWith(5);
    expect(unshareMutate).not.toHaveBeenCalled();
  });

  it("does not publish if the confirm is dismissed", async () => {
    vi.spyOn(globalThis, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    renderToggle({ shareStatus: "personal" });
    await user.click(screen.getByRole("switch"));
    expect(shareMutate).not.toHaveBeenCalled();
  });

  it("owner + shared → checked switch; disabling unpublishes (no confirm)", async () => {
    const user = userEvent.setup();
    renderToggle({ shareStatus: "shared" });
    const sw = screen.getByRole("switch");
    expect((sw as HTMLInputElement).checked).toBe(true);

    await user.click(sw);
    expect(unshareMutate).toHaveBeenCalledWith(5);
    expect(shareMutate).not.toHaveBeenCalled();
  });

  it("disables the switch while a share job is in flight", () => {
    renderToggle({ shareStatus: "sharing" });
    expect((screen.getByRole("switch") as HTMLInputElement).disabled).toBe(true);
  });

  it("surfaces an error when the publish call fails", async () => {
    shareMutate.mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    renderToggle({ shareStatus: "personal" });
    await user.click(screen.getByRole("switch"));
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("non-owner renders a read-only indicator with no switch or mutation", async () => {
    renderToggle({ isOwner: false, shareStatus: "shared" });
    expect(screen.getByTestId("share-indicator")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(shareMutate).not.toHaveBeenCalled();
    expect(unshareMutate).not.toHaveBeenCalled();
  });

  it("non-owner shows 'Shared by {member}' when attributed (LUM-585)", () => {
    renderToggle({ isOwner: false, shareStatus: "shared", sharedBy: "Alex" });
    expect(screen.getByTestId("share-indicator")).toHaveTextContent("Shared by Alex");
  });

  it("non-owner falls back to the generic indicator when unattributed", () => {
    renderToggle({ isOwner: false, shareStatus: "shared", sharedBy: null });
    expect(screen.getByTestId("share-indicator")).toHaveTextContent(
      "Shared with your household",
    );
  });
});
