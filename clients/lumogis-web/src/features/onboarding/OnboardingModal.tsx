// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useCallback, useMemo, useRef, useState, type JSX } from "react";
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
  // Mirrors `busy` for the guards below. `setBusy` is async, so a second entry
  // arriving in the same tick would still read the stale `false` from the
  // closure; a ref is the value that is already true when the guard runs.
  const busyRef = useRef(false);
  // Separate from `busyRef`, and not merged with it. `busyRef` clears in the
  // `finally` the moment the PATCH settles — but the modal is still mounted for
  // one more macrotask after that (query-core defers the notify that unmounts
  // it), so on the success path the handoff button is briefly re-enabled while
  // still on screen. A second Enter in that window would re-enter and write
  // again. This latch is set only after a SUCCESSFUL completion and never
  // cleared: the handoff is over, and there is nothing left to do twice.
  const handoffDoneRef = useRef(false);

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
    // LUM-282. `busy` disables every BUTTON, but ESC is not a button: it is a
    // document-level keydown in `ModalFrame` (`:56-60`) that calls `onClose`
    // unconditionally. Without this guard, pressing ESC while a completion is
    // in flight issues a second concurrent PATCH on the same mutation — and the
    // handoff below documents "one intent, one write", which would then be
    // false for the keyboard path.
    if (busyRef.current) return;
    onClearCompleteError();
    busyRef.current = true;
    setBusy(true);
    try {
      await onComplete();
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [onComplete, onClearCompleteError]);

  const onSkip = useCallback(() => {
    void finish();
  }, [finish]);

  /**
   * The handoff. LUM-282.
   *
   * This was `navigate("/me/connectors")` and nothing else, which changed the
   * route *behind* an overlay that never closed: `ModalFrame` holds `#root`
   * `inert` and `aria-hidden` for as long as the gate renders the modal, so the
   * user landed on a page they could not touch, and every exit — Skip, Done,
   * ESC — permanently completed onboarding. No test pressed the button.
   *
   * FOUR things here are load-bearing:
   *
   * 1. `navigate` is INSIDE the `try`, after the `await`. `void finish();
   *    navigate(...)` would navigate on the failure path too and strand the
   *    user behind the overlay again — the original defect, reintroduced by
   *    its own fix.
   * 2. The `catch` is required, not defensive padding. `completeOnboarding`
   *    uses `mutateAsync`, which REJECTS; `finish()` above is `try/finally`
   *    with no catch and both its call sites `void` it, which is why a failed
   *    PATCH on Skip/Done raises an unhandled rejection today. Swallowing here
   *    is safe because the reason is still shown: `completeError` renders it
   *    below.
   * 3. One intent, one write — which takes TWO guards, because `disabled={busy}`
   *    achieves neither half. `busyRef` covers concurrency *during* the request:
   *    ESC is a document-level keydown in `ModalFrame` (`:56-60`) calling
   *    `onClose` → `onSkip` → `finish()` with no button involved, so it walks
   *    straight past a disabled button. `handoffDoneRef` covers the moment
   *    *after* success: `busy` clears when the PATCH settles, but the modal
   *    lives one more macrotask, so the button is briefly enabled and on screen
   *    again. Without the second latch the claim in this line would be false
   *    across the success boundary — which is how it was first written.
   * 4. `state: { fromOnboarding: true }` is how focus survives. `ModalFrame`'s
   *    restoration (`prev?.isConnected && prev.focus()`) was written for
   *    same-page dismissal; this is the first close-by-navigation in the app,
   *    so `prev` is disconnected and focus would fall to <body>. `SourcesView`
   *    reads this state and focuses its heading — and because `#root` is still
   *    `inert` when it first tries, it verifies the attempt landed and retries.
   *    See the comment on its heading ref; jsdom cannot prove that part.
   *
   * Nothing here depends on the ORDER of the cache write and the navigation.
   * `mutateAsync` resolves after `onSuccess` has called `setQueryData`, but
   * query-core's `notifyManager` defers observer notification by a macrotask,
   * so `navigate` runs first and the modal is briefly mounted on the new route.
   * That is fine because `<OnboardingGate />` sits outside `<Routes>`
   * (`App.tsx:173`), so the route change neither unmounts nor remounts it.
   */
  const onGoToSources = useCallback(async () => {
    if (busyRef.current || handoffDoneRef.current) return;
    onClearCompleteError();
    busyRef.current = true;
    setBusy(true);
    try {
      await onComplete();
      handoffDoneRef.current = true;
      navigate("/me/sources", { state: { fromOnboarding: true } });
    } catch {
      // Intentionally swallowed — see 2 above. `completeError` carries the
      // reason to the user; rethrowing would only re-raise the unhandled
      // rejection this chunk exists to avoid.
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [onComplete, onClearCompleteError, navigate]);

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
        onGoToSources={() => void onGoToSources()}
        busy={busy}
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
