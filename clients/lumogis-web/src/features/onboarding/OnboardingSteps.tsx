// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Onboarding step copy only (LUM-165) — compile-time strings; no HTML injection.

import { type JSX } from "react";

/** Stable handbook anchor for remote / LAN access (LUM-158 family). */
export const REMOTE_ACCESS_DOC_URL =
  "https://github.com/lumogis/lumogis/blob/main/docs/README.md";

export interface OnboardingStepsProps {
  step: number;
  onGoToConnectors: () => void;
}

export function OnboardingStepBody({ step, onGoToConnectors }: OnboardingStepsProps): JSX.Element {
  if (step === 0) {
    return (
      <>
        <p style={{ marginTop: 0 }}>Welcome to Lumogis — your self-hosted assistant.</p>
        <p>
          This short tour orients you on how to add context and connect sources. You can skip it any
          time; your choice is remembered for this account.
        </p>
      </>
    );
  }
  if (step === 1) {
    return (
      <>
        <p style={{ marginTop: 0 }}>Add knowledge Lumogis can use when you chat.</p>
        <p>
          Capture notes, uploads, and memories from the Capture and Search areas. What you index
          stays on hardware you control.
        </p>
      </>
    );
  }
  if (step === 2) {
    return (
      <>
        <p style={{ marginTop: 0 }}>Connect sources and capabilities in Settings.</p>
        <p>
          Use <strong>Sources &amp; connectors</strong> to wire read-only feeds (for example
          documents) and optional tools your operator has enabled. Calendar and other connectors
          depend on how this household&apos;s Lumogis is configured.
        </p>
        <p style={{ fontSize: "0.9rem", opacity: 0.9 }}>
          Need access away from home? See the{" "}
          <a href={REMOTE_ACCESS_DOC_URL} target="_blank" rel="noopener noreferrer">
            deployment and remote access docs
          </a>{" "}
          for Tailscale, tunnels, and similar patterns.
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginTop: "0.75rem" }}>
          <button type="button" onClick={onGoToConnectors}>
            Open connectors
          </button>
        </div>
      </>
    );
  }
  return (
    <>
      <p style={{ marginTop: 0 }}>You&apos;re set.</p>
      <p>
        You can revisit connectors, models, and permissions any time under Settings. When you&apos;re
        ready, start a chat from the tab bar.
      </p>
    </>
  );
}
