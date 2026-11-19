// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-881 — the exact sentence and link that explain why cloud models are off
// because of privacy mode (ADR 147). Pure: no React, no fetching. The
// show-condition is `catalogHasPrivacyBlock`, shared by every surface.

import type { ModelDescriptor } from "../../api/models";
import type { MePrivacyModeResponse } from "../../api/privacyMode";

export type PrivacyHintSurface = "picker" | "key-save";

export interface PrivacyHintCopy {
  text: string;
  linkTo: string | null;
  linkLabel: string | null;
}

export const ADMIN_PRIVACY_PATH = "/settings/privacy?view=household";
export const ME_PRIVACY_PATH = "/settings/privacy";

/** True when the caller's model list contains a model blocked by privacy mode. */
export function catalogHasPrivacyBlock(models: readonly ModelDescriptor[]): boolean {
  return models.some((m) => m.disabled_reason === "privacy_mode");
}

const ASK_ADMIN = "Ask your household admin if you need them.";

function adminLink(label: string): Pick<PrivacyHintCopy, "linkTo" | "linkLabel"> {
  return { linkTo: ADMIN_PRIVACY_PATH, linkLabel: label };
}

const NO_LINK = { linkTo: null, linkLabel: null } as const;

/**
 * Pick the privacy-block copy, or `null` when nothing should be said.
 * Rows are evaluated top to bottom; the first match wins (plan LUM-881,
 * "Copy"). Whether to render at all is decided by the caller with
 * `catalogHasPrivacyBlock`.
 */
export function privacyBlockCopy(args: {
  me: MePrivacyModeResponse | null;
  isAdmin: boolean;
  surface: PrivacyHintSurface;
  adminTier?: boolean;
}): PrivacyHintCopy | null {
  const { me, isAdmin, surface, adminTier = false } = args;
  const picker = surface === "picker";

  // Row 1 — the policy allows cloud: nothing to explain.
  if (me !== null && me.privacy_effective === "allow_cloud") return null;

  // Row 2 — the policy fetch failed. The picker still explains; key-save does
  // not claim a block it could not confirm.
  if (me === null) {
    if (!picker) return null;
    return isAdmin
      ? {
          text: "Cloud models are off because privacy mode is local-only.",
          ...adminLink("Privacy mode settings"),
        }
      : {
          text: `Cloud models are off because privacy mode is local-only. ${ASK_ADMIN}`,
          ...NO_LINK,
        };
  }

  const instanceLocal = me.instance.privacy_effective === "local_only";

  // Row 3 — household privacy mode is locked to local-only.
  if (instanceLocal && me.instance.privacy_mode_locked) {
    if (isAdmin) {
      return {
        text: picker
          ? "Cloud models are off: household privacy mode is locked to local-only."
          : "Cloud models stay off: household privacy mode is locked to local-only.",
        ...adminLink("Unlock it in Privacy mode"),
      };
    }
    return {
      text: picker
        ? `Cloud models are off: your household's privacy mode is locked to local-only. ${ASK_ADMIN}`
        : `Cloud models stay off: your household's privacy mode is locked to local-only. ${ASK_ADMIN}`,
      ...NO_LINK,
    };
  }

  // Row 4 — household privacy mode is local-only.
  if (instanceLocal) {
    if (isAdmin) {
      return {
        text: picker
          ? "Cloud models are off because household privacy mode is local-only."
          : "Cloud models stay off while household privacy mode is local-only.",
        ...adminLink("Allow cloud models in Privacy mode"),
      };
    }
    return {
      text: picker
        ? `Cloud models are off because your household's privacy mode is local-only. ${ASK_ADMIN}`
        : `Cloud models stay off while your household's privacy mode is local-only. ${ASK_ADMIN}`,
      ...NO_LINK,
    };
  }

  // Row 5 — the instance allows cloud; this account limited itself to local.
  // The admin's own restriction says nothing about the tier being edited.
  if (!picker && adminTier) return null;
  return {
    text: picker
      ? "Cloud models are off because you limited your account to local models."
      : "Cloud models stay off for you because you limited your account to local models.",
    // The Me page hides its toggle under a lock, so a link there would dead-end.
    ...(me.instance.privacy_mode_locked
      ? NO_LINK
      : { linkTo: ME_PRIVACY_PATH, linkLabel: "Change it in your privacy settings" }),
  };
}
