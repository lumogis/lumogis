// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The dictation hook is deliberately context-free: it takes an injected
// transcribe fn and never calls useAuth. These tests render it with NO
// <AuthProvider>, proving it can live inside the auth-free EmailComposer.

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
