// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// First-run onboarding modal shell (LUM-165).

import { useCallback, useState, type JSX } from "react";
import { useNavigate } from "react-router-dom";

import { ModalFrame } from "./modalFrame";
import { OnboardingStepBody } from "./OnboardingSteps";

const LAST_STEP = 3;
const TITLE_ID = "lumogis-onboarding-title";

export interface OnboardingModalProps {
  onComplete: () => Promise<void>;
  /** User-visible PATCH failure (already mapped). */
  completeError: string | null;
  onClearCompleteError: () => void;
}

export function OnboardingModal({
  onComplete,
  completeError,
  onClearCompleteError,
}: OnboardingModalProps): JSX.Element {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

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
    setStep((s) => Math.min(LAST_STEP, s + 1));
  }, [onClearCompleteError]);

  const onBack = useCallback(() => {
    onClearCompleteError();
    setStep((s) => Math.max(0, s - 1));
  }, [onClearCompleteError]);

  const onDone = useCallback(() => {
    void finish();
  }, [finish]);

  const stepLabel =
    step === 0
      ? "Welcome"
      : step === 1
        ? "Add knowledge"
        : step === 2
          ? "Connect sources"
          : "Done";

  return (
    <ModalFrame open titleId={TITLE_ID} onClose={onSkip}>
      <h2 id={TITLE_ID} style={{ marginTop: 0 }}>
        {stepLabel}
      </h2>
      <OnboardingStepBody step={step} onGoToConnectors={onGoToConnectors} />
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
        {step < LAST_STEP ? (
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
