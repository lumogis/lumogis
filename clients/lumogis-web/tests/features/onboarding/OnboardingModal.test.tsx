// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { OnboardingModal } from "../../../src/features/onboarding/OnboardingModal";

describe("OnboardingModal", () => {
  it("Skip calls onComplete once", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn().mockResolvedValue(undefined);
    const clear = vi.fn();
    render(
      <MemoryRouter>
        <OnboardingModal
          inviteOnboarding={null}
          onComplete={onComplete}
          completeError={null}
          onClearCompleteError={clear}
        />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: /^skip$/i }));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("Escape skips (and so persists the dismissal), but a click outside the dialog does not", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <OnboardingModal
          inviteOnboarding={null}
          onComplete={onComplete}
          completeError={null}
          onClearCompleteError={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(document.body);
    expect(onComplete).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1));
  });

  it("shows a completion failure as an alert", () => {
    render(
      <MemoryRouter>
        <OnboardingModal
          inviteOnboarding={null}
          onComplete={vi.fn()}
          completeError="Could not save"
          onClearCompleteError={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save");
  });

  it("Next advances step copy", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <OnboardingModal
          inviteOnboarding={null}
          onComplete={vi.fn()}
          completeError={null}
          onClearCompleteError={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Welcome" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^next$/i }));
    expect(screen.getByRole("heading", { name: "Add knowledge" })).toBeInTheDocument();
  });

  it("invite hint adds Household as first step", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <OnboardingModal
          inviteOnboarding={{ allows_shared: true }}
          onComplete={vi.fn()}
          completeError={null}
          onClearCompleteError={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Household" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^next$/i }));
    expect(screen.getByRole("heading", { name: "Welcome" })).toBeInTheDocument();
  });

  // §7a re-review finding. On the success path `busy` clears the moment the
  // PATCH settles, but the gate needs one more macrotask to unmount the modal —
  // so the handoff button is briefly enabled and still on screen. This renders
  // OnboardingModal DIRECTLY, which never self-dismisses, precisely to hold the
  // component in that window and prove the second write is refused.
  it("refuses a second handoff after the first has succeeded", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <OnboardingModal
          inviteOnboarding={null}
          onComplete={onComplete}
          completeError={null}
          onClearCompleteError={() => {}}
        />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: /^Next$/ }));
    await user.click(screen.getByRole("button", { name: /^Next$/ }));
    const handoff = screen.getByRole("button", { name: /Set up documents now/ });

    await user.click(handoff);
    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1));
    // Re-enabled, still mounted — and pressing it again must write nothing.
    expect(handoff).toBeEnabled();
    await user.click(handoff);
    await user.click(handoff);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});
