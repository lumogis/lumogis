// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../src/api/client";
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

  it("shows Listening… in a status region while recording, with the static meter", async () => {
    const h = installMockMediaRecorder(); // jsdom has no AudioContext → level is null
    const user = userEvent.setup();
    render(<VoiceDictationButton transcribe={async () => "x"} onTranscript={vi.fn()} />);
    expect(screen.queryByText("Listening…")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Dictate" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Listening…");
    const meter = screen.getByTestId("voice-level");
    expect(meter).toHaveAttribute("data-static");
    h.uninstall();
  });

  it("shows the failure as visible text, not only a tooltip", async () => {
    const h = installMockMediaRecorder();
    const user = userEvent.setup();
    render(
      <VoiceDictationButton
        transcribe={async () => {
          throw new ApiError(503, "m", "stt_model_missing");
        }}
        onTranscript={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Dictate" }));
    await user.click(screen.getByRole("button", { name: "Stop dictation" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(
      "The speech model is still downloading. Try again in a minute.",
    );
    h.uninstall();
  });

  it("on an insecure page renders a disabled button and the secure-address reason", () => {
    vi.stubGlobal("isSecureContext", false);
    vi.stubGlobal("MediaRecorder", undefined);
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: undefined });
    render(<VoiceDictationButton transcribe={async () => "x"} onTranscript={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Dictate" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(/secure address/);
  });

  it("on a secure page without MediaRecorder still renders nothing", () => {
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("MediaRecorder", undefined);
    const { container } = render(
      <VoiceDictationButton transcribe={async () => "x"} onTranscript={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
