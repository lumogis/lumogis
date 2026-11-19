// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The dictation hook is deliberately context-free: it takes an injected
// transcribe fn and never calls useAuth. These tests render it with NO
// <AuthProvider>, proving it can live inside the auth-free EmailComposer.

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../src/api/client";
import { useVoiceDictation } from "../../src/hooks/useVoiceDictation";
import { installMockMediaRecorder } from "../helpers/mockMediaRecorder";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useVoiceDictation", () => {
  it("stop → injected transcribe → onTranscript", async () => {
    const h = installMockMediaRecorder();
    const transcribe = vi.fn(async () => "hello world");
    const onTranscript = vi.fn();
    const { result } = renderHook(() =>
      useVoiceDictation({ transcribe, onTranscript }),
    );
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.status).toBe("recording");
    await act(async () => {
      await result.current.stop();
    });
    expect(transcribe).toHaveBeenCalledTimes(1);
    expect(onTranscript).toHaveBeenCalledWith("hello world");
    expect(result.current.status).toBe("idle");
    h.uninstall();
  });

  it("empty blob → no transcribe call", async () => {
    const h = installMockMediaRecorder();
    const transcribe = vi.fn(async () => "x");
    const onTranscript = vi.fn();
    const { result } = renderHook(() =>
      useVoiceDictation({ transcribe, onTranscript }),
    );
    // stop() with no active recording returns null → no transcribe.
    await act(async () => {
      await result.current.stop();
    });
    expect(transcribe).not.toHaveBeenCalled();
    expect(onTranscript).not.toHaveBeenCalled();
    h.uninstall();
  });

  it("transcribe rejection → error, returns to idle", async () => {
    const h = installMockMediaRecorder();
    const transcribe = vi.fn(async () => {
      throw new Error("boom");
    });
    const onError = vi.fn();
    const { result } = renderHook(() =>
      useVoiceDictation({ transcribe, onTranscript: vi.fn(), onError }),
    );
    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      await result.current.stop();
    });
    expect(onError).toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
    h.uninstall();
  });

  it("maps stt_model_missing to the downloading message, and clears it on the next start", async () => {
    const h = installMockMediaRecorder();
    let fail = true;
    const transcribe = vi.fn(async () => {
      if (fail) throw new ApiError(503, "m", "stt_model_missing");
      return "ok";
    });
    const { result } = renderHook(() =>
      useVoiceDictation({ transcribe, onTranscript: vi.fn() }),
    );
    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      await result.current.stop();
    });
    expect(result.current.error).toBe(
      "The speech model is still downloading. Try again in a minute.",
    );
    fail = false;
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.error).toBeNull();
    expect(result.current.stream).not.toBeNull();
    h.uninstall();
  });

  it("works with no <AuthProvider> in the tree (context-free)", async () => {
    const h = installMockMediaRecorder();
    // Rendering without any provider must not throw.
    const { result } = renderHook(() =>
      useVoiceDictation({ transcribe: async () => "ok", onTranscript: vi.fn() }),
    );
    expect(result.current.supported).toBe(true);
    await act(async () => {
      await result.current.start();
      await result.current.stop();
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));
    h.uninstall();
  });
});
