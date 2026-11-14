// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-653 — the dictation button in ChatComposer: gated on the injected
// voiceTranscribe prop, inserts at the caret, and restores the caret AFTER the
// controlled value commits.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatComposer } from "../../src/components/ChatComposer";
import { installMockMediaRecorder } from "../helpers/mockMediaRecorder";

afterEach(() => {
  vi.unstubAllGlobals();
});

function Harness({ transcript }: { transcript: string }) {
  const [value, setValue] = useState("abc");
  return (
    <ChatComposer
      value={value}
      onChange={setValue}
      onSubmit={() => {}}
      showLabel={false}
      voiceTranscribe={async () => transcript}
    />
  );
}

describe("ChatComposer voice dictation", () => {
  it("renders the dictate button only when voiceTranscribe is provided", () => {
    const { rerender } = render(
      <ChatComposer value="" onChange={() => {}} onSubmit={() => {}} />,
    );
    expect(screen.queryByRole("button", { name: "Dictate" })).toBeNull();

    installMockMediaRecorder();
    rerender(
      <ChatComposer
        value=""
        onChange={() => {}}
        onSubmit={() => {}}
        voiceTranscribe={async () => "x"}
      />,
    );
    expect(screen.getByRole("button", { name: "Dictate" })).toBeInTheDocument();
  });

  it("inserts the transcript at the caret and restores the caret after commit", async () => {
    const h = installMockMediaRecorder();
    const user = userEvent.setup();
    render(<Harness transcript="XY" />);

    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    ta.focus();
    ta.setSelectionRange(2, 2); // caret between "ab" and "c"

    await user.click(screen.getByRole("button", { name: "Dictate" }));
    await user.click(screen.getByRole("button", { name: "Stop dictation" }));

    await waitFor(() => expect(ta.value).toBe("abXYc"));
    // Caret restored to after the inserted text, not jumped to the end.
    expect(ta.selectionStart).toBe(4);
    h.uninstall();
  });
});
