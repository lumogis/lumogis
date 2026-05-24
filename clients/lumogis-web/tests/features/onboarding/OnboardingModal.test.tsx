// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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
        <OnboardingModal onComplete={onComplete} completeError={null} onClearCompleteError={clear} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: /^skip$/i }));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("Next advances step copy", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <OnboardingModal onComplete={vi.fn()} completeError={null} onClearCompleteError={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Welcome" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^next$/i }));
    expect(screen.getByRole("heading", { name: "Add knowledge" })).toBeInTheDocument();
  });
});
