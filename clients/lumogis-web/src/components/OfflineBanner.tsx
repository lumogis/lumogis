// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 3E — UX status strip only (not “offline Lumogis” execution).

/** Copy is intentionally constrained: drafts may remain locally; orchestrator APIs require network. */
export const OFFLINE_BANNER_COPY =
  "You're offline. Drafts you type can stay on this device, but chat, search, approvals, and admin actions need a connection.";

export interface OfflineBannerProps {
  visible: boolean;
}

/**
 * Thin status strip placed under the shell header while offline — does not block navigation or main chrome.
 */
export function OfflineBanner({ visible }: OfflineBannerProps): JSX.Element | null {
  if (!visible) return null;

  return (
    <div className="lumogis-offline-banner" role="status" aria-live="polite" data-testid="lumogis-offline-banner">
      <p className="lumogis-offline-banner__text">{OFFLINE_BANNER_COPY}</p>
    </div>
  );
}
