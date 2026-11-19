// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAudioLevel } from "../../src/hooks/useAudioLevel";

const stream = { id: "s" } as unknown as MediaStream;

interface Spy {
  close: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  resume: ReturnType<typeof vi.fn>;
}

/** A fake AudioContext whose analyser fills a fixed waveform (128 = silence). */
function installAudioContext(opts: { sample?: number; resumeRejects?: boolean } = {}): Spy {
  const spy: Spy = {
    close: vi.fn(async () => undefined),
    disconnect: vi.fn(),
    resume: vi.fn(() =>
      opts.resumeRejects ? Promise.reject(new Error("suspended")) : Promise.resolve(),
    ),
  };
  class FakeAudioContext {
    state = opts.resumeRejects ? "suspended" : "running";
    close = spy.close;
    resume = spy.resume;
    createMediaStreamSource() {
      return { connect: vi.fn(), disconnect: spy.disconnect };
    }
    createAnalyser() {
      return {
        fftSize: 0,
        getByteTimeDomainData(buf: Uint8Array) {
          buf.fill(opts.sample ?? 128);
        },
      };
    }
  }
  vi.stubGlobal("AudioContext", FakeAudioContext);
  return spy;
}

let frames: Array<(t: number) => void> = [];
let now = 0;

function runFrames(): void {
  const batch = frames;
  frames = [];
  now += 100; // beyond the 66 ms throttle
  batch.forEach((cb) => cb(now));
}

beforeEach(() => {
  frames = [];
  now = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: (t: number) => void) => {
    frames.push(cb);
    return frames.length;
  });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useAudioLevel", () => {
  it("returns null without a stream", () => {
    installAudioContext();
    const { result } = renderHook(() => useAudioLevel(null));
    expect(result.current).toBeNull();
  });

  it("returns null when AudioContext is missing", () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", undefined);
    const { result } = renderHook(() => useAudioLevel(stream));
    expect(result.current).toBeNull();
  });

  it("returns null under prefers-reduced-motion, and never opens an AudioContext", () => {
    const ctor = vi.fn();
    vi.stubGlobal("AudioContext", ctor);
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }));
    const { result } = renderHook(() => useAudioLevel(stream));
    expect(result.current).toBeNull();
    expect(ctor).not.toHaveBeenCalled();
  });

  it("reports a level between 0 and 1 from the analyser", () => {
    installAudioContext({ sample: 255 }); // full-scale
    const { result } = renderHook(() => useAudioLevel(stream));
    expect(result.current).toBe(0);
    act(() => runFrames());
    expect(result.current).toBeGreaterThan(0);
    expect(result.current).toBeLessThanOrEqual(1);
  });

  it("stays at 0 for silence", () => {
    installAudioContext({ sample: 128 });
    const { result } = renderHook(() => useAudioLevel(stream));
    act(() => runFrames());
    expect(result.current).toBe(0);
  });

  it("closes the AudioContext and cancels the frame on unmount", () => {
    const spy = installAudioContext();
    const { unmount } = renderHook(() => useAudioLevel(stream));
    unmount();
    expect(spy.close).toHaveBeenCalled();
    expect(spy.disconnect).toHaveBeenCalled();
    expect(globalThis.cancelAnimationFrame).toHaveBeenCalled();
  });

  it("closes the AudioContext when the stream goes away", () => {
    const spy = installAudioContext();
    const { rerender } = renderHook(({ s }) => useAudioLevel(s), {
      initialProps: { s: stream as MediaStream | null },
    });
    rerender({ s: null });
    expect(spy.close).toHaveBeenCalled();
  });

  it("does not throw when the context is suspended and resume rejects", async () => {
    installAudioContext({ resumeRejects: true });
    const { result } = renderHook(() => useAudioLevel(stream));
    await act(async () => {
      runFrames();
      await Promise.resolve();
    });
    expect(result.current).toBe(0);
  });
});
