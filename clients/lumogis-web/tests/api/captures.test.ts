// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it } from "vitest";

import { formatCaptureErrorMessage } from "../../src/api/captures";

const body = (code: string) => JSON.stringify({ detail: { code, message: "server words" } });

describe("formatCaptureErrorMessage — speech-to-text codes (LUM-995)", () => {
  it("says the model is still downloading and that the audio is saved", () => {
    expect(formatCaptureErrorMessage(503, body("stt_model_missing"))).toBe(
      "The speech model is still downloading. Your audio is saved; transcribe it again in a minute.",
    );
  });

  it("says the engine isn't answering yet and that the audio is saved", () => {
    expect(formatCaptureErrorMessage(503, body("stt_unreachable"))).toBe(
      "The speech engine isn't answering yet. Your audio is saved; transcribe it again in a few minutes.",
    );
  });

  it("keeps the existing copy for the two older codes", () => {
    const old =
      "Transcription is unavailable on the server. Your audio is still saved as an attachment if upload succeeded.";
    expect(formatCaptureErrorMessage(503, body("stt_disabled"))).toBe(old);
    expect(formatCaptureErrorMessage(503, body("stt_processing_error"))).toBe(old);
  });
});
