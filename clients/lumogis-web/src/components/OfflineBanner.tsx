// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 3E — UX status strip only (not “offline Lumogis” execution).

import { WifiOff } from "lucide-react";

import { Alert, AlertDescription } from "./ui/alert";

/** Copy is intentionally constrained: drafts may remain locally; orchestrator APIs require network. */
export const OFFLINE_BANNER_COPY =
  "You're offline. Drafts you type can stay on this device, but chat, search, approvals, and admin actions need a connection.";

export interface OfflineBannerProps {
  visible: boolean;
}

/**
 * Compact notice at the top of the page while offline (ShellBanners, LUM-993) — does not block navigation or main chrome.
 */
export function OfflineBanner({ visible }: OfflineBannerProps): JSX.Element | null {
  if (!visible) return null;

  return (
    <Alert role="status" aria-live="polite" data-testid="lumogis-offline-banner" className="border-warning/40 bg-warning/10">
      <WifiOff aria-hidden="true" />
      <AlertDescription className="text-foreground">{OFFLINE_BANNER_COPY}</AlertDescription>
    </Alert>
  );
}
