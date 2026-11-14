// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAudioRecorder, pickAudioMimeType } from "../../src/hooks/useAudioRecorder";
import { installMockMediaRecorder, MockMediaRecorder } from "../helpers/mockMediaRecorder";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useAudioRecorder", () => {
  it("starts recording and transitions idle→recording", async () => {
    const h = installMockMediaRecorder();
    const { result } = renderHook(() => useAudioRecorder());
    expect(result.current.supported).toBe(true);
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.status).toBe("recording");
    h.uninstall();
  });

  it("stop returns the assembled Blob and stops tracks", async () => {
    const h = installMockMediaRecorder();
    const { result } = renderHook(() => useAudioRecorder());
    await act(async () => {
      await result.current.start();
    });
    let blob: Blob | null = null;
    await act(async () => {
      blob = await result.current.stop();
    });
    expect(blob).toBeInstanceOf(Blob);
    expect(blob!.size).toBeGreaterThan(0);
    expect(result.current.status).toBe("idle");
    expect(h.trackStops[0]).toHaveBeenCalled();
    h.uninstall();
  });

  it("refuses when no supported mime (preserves QuickCapture behaviour)", async () => {
    const onError = vi.fn();
    const h = installMockMediaRecorder({ supported: false }); // isTypeSupported → false
    const { result } = renderHook(() => useAudioRecorder({ onError }));
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.status).toBe("error");
    expect(result.current.error).toMatch(/not supported/i);
    expect(onError).toHaveBeenCalled();
    h.uninstall();
  });

  it("supported=false when MediaRecorder is absent", () => {
    vi.stubGlobal("MediaRecorder", undefined);
    const { result } = renderHook(() => useAudioRecorder());
    expect(result.current.supported).toBe(false);
  });

  it("permission denied → error, no hot mic", async () => {
    const onError = vi.fn();
    installMockMediaRecorder();
    // Override getUserMedia to reject.
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn(async () => {
          throw new DOMException("denied", "NotAllowedError");
        }),
      },
    });
    const { result } = renderHook(() => useAudioRecorder({ onError }));
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.status).toBe("error");
    expect(onError).toHaveBeenCalled();
  });

  it("wires recorder.onerror → error state", async () => {
    const onError = vi.fn();
    const h = installMockMediaRecorder();
    const { result } = renderHook(() => useAudioRecorder({ onError }));
    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      (h.last as MockMediaRecorder).triggerError();
    });
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(onError).toHaveBeenCalled();
    h.uninstall();
  });

  it("double start is a no-op", async () => {
    const h = installMockMediaRecorder();
    const gum = navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>;
    const { result } = renderHook(() => useAudioRecorder());
    await act(async () => {
      await result.current.start();
      await result.current.start();
    });
    expect(gum).toHaveBeenCalledTimes(1);
    h.uninstall();
  });

  it("pickAudioMimeType returns '' when nothing supported", () => {
    installMockMediaRecorder({ supported: false });
    expect(pickAudioMimeType()).toBe("");
  });
});
