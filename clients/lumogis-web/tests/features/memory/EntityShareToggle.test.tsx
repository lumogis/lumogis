// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest unit — EntityShareToggle (LUM-581).

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { EntityShareToggle } from "../../../src/features/memory/EntityShareToggle";

const shareMutate = vi.fn();
const unshareMutate = vi.fn();

vi.mock("../../../src/features/memory/useEntitySharing", async () => {
  const actual = await vi.importActual<
    typeof import("../../../src/features/memory/useEntitySharing")
  >("../../../src/features/memory/useEntitySharing");
  return {
    ...actual,
    usePublishEntity: () => ({ mutateAsync: shareMutate, isPending: false }),
    useUnpublishEntity: () => ({ mutateAsync: unshareMutate, isPending: false }),
  };
});

const CLIENT = {} as never;
const ENTITY_ID = "ent-abc";

function renderToggle(
  overrides: Partial<Parameters<typeof EntityShareToggle>[0]> = {},
) {
  return render(
    <EntityShareToggle
      client={CLIENT}
      entityId={ENTITY_ID}
      displayName="Lumogis"
      shareStatus="personal"
      isOwner
      {...overrides}
    />,
  );
}

describe("EntityShareToggle", () => {
  beforeEach(() => {
    shareMutate.mockReset().mockResolvedValue(undefined);
    unshareMutate.mockReset().mockResolvedValue(undefined);
  });

  it("owner + personal → unchecked switch; enabling asks in a dialog, then publishes", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    renderToggle({ shareStatus: "personal", onChanged });
    const sw = screen.getByRole("switch");
    expect(sw).toHaveAttribute("aria-checked", "false");

    await user.click(sw);
    // Sharing is a privacy boundary: nothing happens until the dialog is confirmed.
    const dialog = await screen.findByRole("alertdialog");
    expect(shareMutate).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Share" }));
    expect(shareMutate).toHaveBeenCalledWith(ENTITY_ID);
    expect(unshareMutate).not.toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalled();
  });

  it("does not publish if the dialog is cancelled", async () => {
    const user = userEvent.setup();
    renderToggle({ shareStatus: "personal" });
    await user.click(screen.getByRole("switch"));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(shareMutate).not.toHaveBeenCalled();
  });

  it("owner + shared → checked switch; disabling unpublishes (no confirm)", async () => {
    const user = userEvent.setup();
    renderToggle({ shareStatus: "shared" });
    const sw = screen.getByRole("switch");
    expect(sw).toHaveAttribute("aria-checked", "true");

    await user.click(sw);
    expect(unshareMutate).toHaveBeenCalledWith(ENTITY_ID);
    expect(shareMutate).not.toHaveBeenCalled();
  });

  it("surfaces an error and keeps prior state when publish fails", async () => {
    shareMutate.mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    const onChanged = vi.fn();
    renderToggle({ shareStatus: "personal", onChanged });
    await user.click(screen.getByRole("switch"));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Share" }),
    );
    expect(await screen.findByRole("alert")).toBeTruthy();
    // Prior state preserved: still shown as not-shared, onChanged not fired.
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("non-owner renders a read-only indicator with no switch or mutation", () => {
    renderToggle({ isOwner: false, shareStatus: "shared" });
    expect(screen.getByTestId("entity-share-indicator")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(shareMutate).not.toHaveBeenCalled();
    expect(unshareMutate).not.toHaveBeenCalled();
  });
});
