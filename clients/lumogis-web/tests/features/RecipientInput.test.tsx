// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Recipient completion in the composer (LUM-818).
//
// The test that matters most is `inserts the bare address`: the backend's
// `_validate_recipient` rejects whitespace, so inserting the rendered
// "Jane Smith <j@x.com>" form fails as a 422 at proposal-create — long after
// the click, with nothing on screen connecting the two.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { RecipientSuggestion } from "../../src/api/emailRecipients";
import { RecipientInput } from "../../src/features/email/RecipientInput";

const JANE: RecipientSuggestion = {
  address: "jane@example.com",
  display_name: "Jane Smith",
  corresponded: true,
};
const JAMES: RecipientSuggestion = {
  address: "james@example.com",
  display_name: "",
  corresponded: false,
};

function Harness(props: {
  suggest: (q: string, signal?: AbortSignal) => Promise<RecipientSuggestion[]>;
  initial?: string;
}) {
  const [value, setValue] = useState(props.initial ?? "");
  return (
    <>
      <RecipientInput
        suggest={props.suggest}
        value={value}
        onChange={setValue}
        label="To"
        placeholder="To"
        className="lumogis-composer__to"
      />
      <output data-testid="value">{value}</output>
    </>
  );
}

const suggestBoth = (_q: string, _signal?: AbortSignal): Promise<RecipientSuggestion[]> =>
  Promise.resolve([JANE, JAMES]);

describe("RecipientInput", () => {
  it("offers matches once the query is long enough", async () => {
    render(<Harness suggest={suggestBoth} />);

    fireEvent.change(screen.getByLabelText("To"), { target: { value: "ja" } });

    expect(await screen.findByText("jane@example.com")).toBeTruthy();
    expect(screen.getByText("Jane Smith")).toBeTruthy();
  });

  it("does not query below the minimum length", async () => {
    const suggest = vi.fn(suggestBoth);
    render(<Harness suggest={suggest} />);

    fireEvent.change(screen.getByLabelText("To"), { target: { value: "j" } });

    await new Promise((r) => setTimeout(r, 250));
    expect(suggest).not.toHaveBeenCalled();
  });

  it("inserts the bare address, never the rendered name form", async () => {
    render(<Harness suggest={suggestBoth} />);
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "ja" } });

    fireEvent.mouseDown(await screen.findByText("jane@example.com"));

    await waitFor(() => {
      const v = screen.getByTestId("value").textContent ?? "";
      expect(v).toContain("jane@example.com");
      expect(v).not.toContain("Jane Smith");
      expect(v).not.toContain("<");
    });
  });

  it("completes only the segment being typed, keeping earlier recipients", async () => {
    render(<Harness suggest={suggestBoth} initial="alice@example.com, " />);
    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "alice@example.com, ja" },
    });

    fireEvent.mouseDown(await screen.findByText("jane@example.com"));

    await waitFor(() => {
      const v = screen.getByTestId("value").textContent ?? "";
      expect(v).toContain("alice@example.com");
      expect(v).toContain("jane@example.com");
    });
  });

  it("queries only the active segment, not the whole field", async () => {
    const suggest = vi.fn(suggestBoth);
    render(<Harness suggest={suggest} />);

    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "alice@example.com, ja" },
    });

    await waitFor(() => expect(suggest).toHaveBeenCalled());
    expect(suggest.mock.calls[0][0]).toBe("ja");
  });

  it("is keyboard navigable and selects with Enter", async () => {
    render(<Harness suggest={suggestBoth} />);
    const input = screen.getByLabelText("To");
    fireEvent.change(input, { target: { value: "ja" } });
    await screen.findByText("jane@example.com");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(screen.getByTestId("value").textContent).toContain("james@example.com"),
    );
  });

  it("Escape dismisses the list without clearing what was typed", async () => {
    render(<Harness suggest={suggestBoth} />);
    const input = screen.getByLabelText("To");
    fireEvent.change(input, { target: { value: "ja" } });
    await screen.findByText("jane@example.com");

    fireEvent.keyDown(input, { key: "Escape" });

    await waitFor(() => expect(screen.queryByText("jane@example.com")).toBeNull());
    expect(screen.getByTestId("value").textContent).toBe("ja");
  });

  it("a failed lookup leaves the user typing normally", async () => {
    const suggest = () => Promise.reject(new Error("network"));
    render(<Harness suggest={suggest} />);

    fireEvent.change(screen.getByLabelText("To"), { target: { value: "ja" } });

    await new Promise((r) => setTimeout(r, 250));
    expect(screen.getByTestId("value").textContent).toBe("ja");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("renders a sent-only correspondent without a display name", async () => {
    render(<Harness suggest={() => Promise.resolve([JAMES])} />);

    fireEvent.change(screen.getByLabelText("To"), { target: { value: "ja" } });

    expect(await screen.findByText("james@example.com")).toBeTruthy();
  });
});
