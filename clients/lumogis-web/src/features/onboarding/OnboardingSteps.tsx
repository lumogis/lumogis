// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { type JSX } from "react";

import type { InviteOnboardingHintStored } from "../../api/invites";

/** Stable handbook anchor for remote / LAN access (LUM-158 family). */
export const REMOTE_ACCESS_DOC_URL =
  "https://github.com/lumogis/lumogis/blob/main/docs/README.md";

export interface OnboardingStepsProps {
  step: number;
  inviteOnboarding?: InviteOnboardingHintStored | null;
  /** LUM-282: renamed from `onGoToConnectors`. The old name outlived the old
   *  destination, and a name that points somewhere the code does not go is how
   *  the next reader learns the wrong thing. */
  onGoToSources: () => void;
  /** True while the completion PATCH is in flight — the handoff button joins
   *  the same disabled set as Back/Skip/Next/Done, so one intent is one write. */
  busy: boolean;
}

function HouseholdStep({ allowsShared }: { allowsShared: boolean }): JSX.Element {
  return (
    <>
      <p style={{ marginTop: 0 }}>Welcome to your Lumogis household.</p>
      {allowsShared ? (
        <p>
          You can add personal memories that stay private to you, and browse shared household
          knowledge that other members contribute. Admins manage who can join and how roles work.
        </p>
      ) : (
        <p>
          Your personal memories stay private to you. Shared household rows may still exist for
          other members — this tour explains how Lumogis scopes knowledge on your account.
        </p>
      )}
      <p style={{ fontSize: "0.9rem", opacity: 0.9 }}>
        Per-user visibility controls for shared content are evolving; ask your household admin if
        you are unsure what others can see.
      </p>
    </>
  );
}

function WelcomeStep(): JSX.Element {
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

function AddKnowledgeStep(): JSX.Element {
  return (
    <>
      <p style={{ marginTop: 0 }}>Add knowledge Lumogis can use when you chat.</p>
      <p>
        Capture notes, uploads, and memories from the Capture and Search areas. What you index stays
        on hardware you control.
      </p>
    </>
  );
}

function ConnectSourcesStep({
  onGoToSources,
  busy,
}: {
  onGoToSources: () => void;
  busy: boolean;
}): JSX.Element {
  return (
    <>
      <p style={{ marginTop: 0 }}>Give Lumogis something to read.</p>
      <p>
        <strong>Sources</strong> are the document archives, feeds and pages Lumogis watches
        for you. If you keep scanned documents in <strong>Paperless-ngx</strong>, that is the
        one most people start with — it takes the server address and an API token.
      </p>
      <p>
        You can skip this and set it up later from <strong>Sources</strong> or{" "}
        <strong>Connectors</strong> under Settings.
      </p>
      <p style={{ fontSize: "0.9rem", opacity: 0.9 }}>
        Need access away from home? See the{" "}
        <a href={REMOTE_ACCESS_DOC_URL} target="_blank" rel="noopener noreferrer">
          deployment and remote access docs
        </a>{" "}
        for Tailscale, tunnels, and similar patterns.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginTop: "0.75rem" }}>
        {/* The label states BOTH facts it must not lose to rewording: where it
            goes, and that it ends the tour. This is a one-way door — onboarding
            completion is recorded at most once and there is no un-complete —
            so a label reading only "Open sources" would be a lie by omission. */}
        <button type="button" onClick={onGoToSources} disabled={busy}>
          Set up documents now — this finishes the tour
        </button>
      </div>
    </>
  );
}

function DoneStep(): JSX.Element {
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

export function OnboardingStepBody({
  step,
  inviteOnboarding,
  onGoToSources,
  busy,
}: OnboardingStepsProps): JSX.Element {
  if (inviteOnboarding) {
    if (step === 0) return <HouseholdStep allowsShared={inviteOnboarding.allows_shared} />;
    if (step === 1) return <WelcomeStep />;
    if (step === 2) return <AddKnowledgeStep />;
    if (step === 3) return <ConnectSourcesStep onGoToSources={onGoToSources} busy={busy} />;
    return <DoneStep />;
  }
  if (step === 0) return <WelcomeStep />;
  if (step === 1) return <AddKnowledgeStep />;
  if (step === 2) return <ConnectSourcesStep onGoToSources={onGoToSources} busy={busy} />;
  return <DoneStep />;
}
