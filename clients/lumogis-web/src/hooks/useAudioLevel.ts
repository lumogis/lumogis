// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-995 — turns a live microphone stream into a 0–1 level for the dictation meter.
// Reads the stream in-page through an AnalyserNode; it never records, stores or
// sends audio. Returns `null` to mean "draw the static dot": no stream, no
// AudioContext, or the person asked for reduced motion.

import { useEffect, useState } from "react";

/** At most this often (ms), and only when the level moves by this much. */
const MIN_INTERVAL_MS = 66;
const MIN_DELTA = 0.02;

type AudioContextCtor = typeof AudioContext;

function audioContextCtor(): AudioContextCtor | undefined {
  if (typeof window === "undefined") return undefined;
  const w = window as unknown as {
    AudioContext?: AudioContextCtor;
    webkitAudioContext?: AudioContextCtor;
  };
  return w.AudioContext ?? w.webkitAudioContext;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function useAudioLevel(stream: MediaStream | null): number | null {
  const [level, setLevel] = useState(0);
  const Ctor = audioContextCtor();
  const animated = stream !== null && Ctor !== undefined && !prefersReducedMotion();

  useEffect(() => {
    setLevel(0);
    if (!stream || !Ctor || prefersReducedMotion()) return;

    let ctx: AudioContext;
    try {
      ctx = new Ctor();
    } catch {
      return;
    }
    let raf = 0;
    let stopped = false;
    let last = 0;
    let shown = 0;
    try {
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      // iOS Safari starts an AudioContext suspended: best effort, never fatal. The
      // meter then stays near 0 while "Listening…" still shows.
      void Promise.resolve(ctx.resume()).catch(() => undefined);
      const data = new Uint8Array(analyser.fftSize);

      const tick = (now: number): void => {
        if (stopped) return;
        if (now - last >= MIN_INTERVAL_MS) {
          last = now;
          analyser.getByteTimeDomainData(data);
          let sum = 0;
          for (let i = 0; i < data.length; i++) {
            const v = (data[i] - 128) / 128;
            sum += v * v;
          }
          // RMS, scaled so ordinary speech fills most of the bar.
          const next = Math.min(1, Math.sqrt(sum / data.length) * 4);
          if (Math.abs(next - shown) >= MIN_DELTA) {
            shown = next;
            setLevel(next);
          }
        }
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);

      return () => {
        stopped = true;
        cancelAnimationFrame(raf);
        try {
          source.disconnect();
        } catch {
          /* already disconnected */
        }
        void Promise.resolve(ctx.close()).catch(() => undefined);
      };
    } catch {
      void Promise.resolve(ctx.close()).catch(() => undefined);
      return;
    }
  }, [stream, Ctor]);

  return animated ? level : null;
}
