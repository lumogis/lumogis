// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useCallback, useMemo, useState, type JSX } from "react";
import { useNavigate } from "react-router-dom";

import type { InviteOnboardingHintStored } from "../../api/invites";
import { ModalFrame } from "./modalFrame";
import { OnboardingStepBody } from "./OnboardingSteps";

const DEFAULT_LAST_STEP = 3;
const INVITE_LAST_STEP = 4;
const TITLE_ID = "lumogis-onboarding-title";

export interface OnboardingModalProps {
  inviteOnboarding: InviteOnboardingHintStored | null;
  onComplete: () => Promise<void>;
  completeError: string | null;
  onClearCompleteError: () => void;
}

export function OnboardingModal({
  inviteOnboarding,
  onComplete,
  completeError,
  onClearCompleteError,
}: OnboardingModalProps): JSX.Element {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

  const lastStep = inviteOnboarding ? INVITE_LAST_STEP : DEFAULT_LAST_STEP;

  const stepLabel = useMemo(() => {
    if (inviteOnboarding) {
      if (step === 0) return "Household";
      if (step === 1) return "Welcome";
      if (step === 2) return "Add knowledge";
      if (step === 3) return "Connect sources";
      return "Done";
    }
    if (step === 0) return "Welcome";
    if (step === 1) return "Add knowledge";
    if (step === 2) return "Connect sources";
    return "Done";
  }, [inviteOnboarding, step]);

  const finish = useCallback(async () => {
    onClearCompleteError();
    setBusy(true);
    try {
      await onComplete();
    } finally {
      setBusy(false);
    }
  }, [onComplete, onClearCompleteError]);

  const onSkip = useCallback(() => {
    void finish();
  }, [finish]);

  const onGoToConnectors = useCallback(() => {
    navigate("/me/connectors");
  }, [navigate]);

  const onNext = useCallback(() => {
    onClearCompleteError();
    setStep((s) => Math.min(lastStep, s + 1));
  }, [onClearCompleteError, lastStep]);

  const onBack = useCallback(() => {
    onClearCompleteError();
    setStep((s) => Math.max(0, s - 1));
  }, [onClearCompleteError]);

  const onDone = useCallback(() => {
    void finish();
  }, [finish]);

  return (
    <ModalFrame open titleId={TITLE_ID} onClose={onSkip}>
      <h2 id={TITLE_ID} style={{ marginTop: 0 }}>
        {stepLabel}
      </h2>
      <OnboardingStepBody
        step={step}
        inviteOnboarding={inviteOnboarding}
        onGoToConnectors={onGoToConnectors}
      />
      {completeError !== null && completeError.length > 0 ? (
        <p role="alert" style={{ color: "salmon", fontSize: "0.9rem" }}>
          {completeError}
        </p>
      ) : null}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginTop: "1rem" }}>
        {step > 0 ? (
          <button type="button" onClick={onBack} disabled={busy}>
            Back
          </button>
        ) : null}
        <button type="button" onClick={onSkip} disabled={busy}>
          Skip
        </button>
        {step < lastStep ? (
          <button type="button" onClick={onNext} disabled={busy}>
            Next
          </button>
        ) : (
          <button type="button" onClick={onDone} disabled={busy}>
            Done
          </button>
        )}
      </div>
    </ModalFrame>
  );
}

/** Exported for Vitest regression checks (non-invite flow step count). */
export const ONBOARDING_LAST_STEP_WITHOUT_INVITE = DEFAULT_LAST_STEP;
