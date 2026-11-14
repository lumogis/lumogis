// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-653 — a deterministic MediaRecorder + getUserMedia fake for Vitest, so
// hook/component tests can drive a full record→stop→blob cycle without a real
// microphone. install() stubs the globals; the returned handle exposes track
// stop spies and lets a test simulate a mid-recording error.

import { vi } from "vitest";

export interface MockRecorderHandle {
  trackStops: ReturnType<typeof vi.fn>[];
  /** The most recently constructed recorder (for firing onerror in a test). */
  last: MockMediaRecorder | null;
  uninstall: () => void;
}

class MockMediaRecorder extends EventTarget {
  static supported = true;
  static isTypeSupported(_type: string): boolean {
    return MockMediaRecorder.supported;
  }

  state: "inactive" | "recording" = "inactive";
  mimeType: string;
  ondataavailable: ((ev: { data: Blob }) => void) | null = null;
  onerror: (() => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(_stream: MediaStream, options?: { mimeType?: string }) {
    super();
    this.mimeType = options?.mimeType ?? "audio/webm";
    active.last = this;
  }

  start(): void {
    this.state = "recording";
  }

  stop(): void {
    this.state = "inactive";
    // Flush a final chunk, then signal stop (matches real ordering).
    this.ondataavailable?.({ data: new Blob(["audio-bytes"], { type: this.mimeType }) });
    this.onstop?.();
    this.dispatchEvent(new Event("stop"));
  }

  /** Test helper: simulate a mid-recording MediaRecorder error. */
  triggerError(): void {
    this.onerror?.();
  }
}

let active: MockRecorderHandle = { trackStops: [], last: null, uninstall: () => {} };

export function installMockMediaRecorder(
  opts: { supported?: boolean } = {},
): MockRecorderHandle {
  MockMediaRecorder.supported = opts.supported ?? true;
  const trackStops: ReturnType<typeof vi.fn>[] = [];

  const getUserMedia = vi.fn(async () => {
    const stop = vi.fn();
    trackStops.push(stop);
    return { getTracks: () => [{ stop }] } as unknown as MediaStream;
  });

  const prevMR = (globalThis as { MediaRecorder?: unknown }).MediaRecorder;
  const prevMD = navigator.mediaDevices;

  vi.stubGlobal("MediaRecorder", MockMediaRecorder);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });

  active = {
    trackStops,
    last: null,
    uninstall: () => {
      vi.stubGlobal("MediaRecorder", prevMR);
      Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: prevMD,
      });
    },
  };
  return active;
}

export { MockMediaRecorder };
