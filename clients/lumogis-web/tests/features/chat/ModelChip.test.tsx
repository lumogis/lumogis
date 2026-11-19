// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// ModelChip (LUM-993 Decision 9): the composer's model choice. Disabled models
// stay listed with the reason; loading and error states are compact.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ModelDescriptor } from "../../../src/api/models";
import { ModelChip } from "../../../src/features/chat/ModelChip";

const model = (over: Partial<ModelDescriptor>): ModelDescriptor =>
  ({ id: "llama", label: "Llama", is_local: true, enabled: true, ...over }) as ModelDescriptor;

const MODELS = [
  model({}),
  model({ id: "claude", label: "Claude", is_local: false, enabled: false, disabled_reason: "privacy_mode" }),
  model({ id: "gpt", label: "GPT", is_local: false, enabled: false, disabled_reason: "no_credentials" }),
  model({ id: "x", label: "X", is_local: false, enabled: false, disabled_reason: "something_new" as ModelDescriptor["disabled_reason"] }),
];

describe("ModelChip", () => {
  it("is a combobox named Model that lists every model, with the reason a model is off", async () => {
    const onChange = vi.fn();
    render(<ModelChip models={MODELS} value="llama" onChange={onChange} error={null} onRetry={() => {}} />);
    const select = screen.getByRole("combobox", { name: "Model" });
    const options = screen.getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "Llama (local)",
      "Claude — off (privacy mode)",
      "GPT — no API key",
      "X — unavailable",
    ]);
    expect(options.map((o) => (o as HTMLOptionElement).disabled)).toEqual([false, true, true, true]);
    await userEvent.selectOptions(select, "llama");
    expect(onChange).toHaveBeenCalledWith("llama");
  });

  it("shows a quiet loading state before the models arrive", () => {
    render(<ModelChip models={[]} value="claude" onChange={() => {}} error={null} onRetry={() => {}} />);
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByText("Loading models…").closest("[aria-busy]")).toHaveAttribute("aria-busy", "true");
  });

  it("says when models cannot be loaded and offers Retry", async () => {
    const onRetry = vi.fn();
    render(<ModelChip models={[]} value="claude" onChange={() => {}} error="timeout" onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Unable to load models: timeout.");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
