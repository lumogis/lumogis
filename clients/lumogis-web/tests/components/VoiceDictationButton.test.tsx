// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceDictationButton } from "../../src/components/VoiceDictationButton";
import { installMockMediaRecorder } from "../helpers/mockMediaRecorder";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("VoiceDictationButton", () => {
  it("renders null when MediaRecorder is unsupported", () => {
    vi.stubGlobal("MediaRecorder", undefined);
    render(
      <VoiceDictationButton transcribe={async () => "x"} onTranscript={vi.fn()} />,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("calls onTranscript on a full record→stop cycle and reports busy", async () => {
    const h = installMockMediaRecorder();
    const onTranscript = vi.fn();
    const onBusyChange = vi.fn();
    const user = userEvent.setup();
    render(
      <VoiceDictationButton
        transcribe={async () => "dictated text"}
        onTranscript={onTranscript}
        onBusyChange={onBusyChange}
      />,
    );
    const btn = screen.getByRole("button", { name: "Dictate" });
    await user.click(btn); // start
    expect(screen.getByRole("button", { name: "Stop dictation" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Stop dictation" })); // stop → transcribe
    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith("dictated text"));
    expect(onBusyChange).toHaveBeenCalledWith(true);
    await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
    h.uninstall();
  });
});
