// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Risk tier badge — parent plan §"Phase 1 Pass 1.4 item 12".
//
// Colour mapping:
//   low       → green  (read-only / search-style)
//   medium    → amber  (write but reversible)
//   high      → red    (write + non-reversible)
//   hard_limit → charcoal/disabled (cannot be elevated)

import type { RiskTier } from "../../api/approvals";

const RISK_LABELS: Record<RiskTier, string> = {
  low: "Low risk",
  medium: "Medium risk",
  high: "High risk",
  hard_limit: "Hard limit",
};

export interface RiskBadgeProps {
  tier: RiskTier;
}

export function RiskBadge({ tier }: RiskBadgeProps): JSX.Element {
  return (
    <span
      className={`lumogis-risk-badge lumogis-risk-badge--${tier.replace("_", "-")}`}
      title={RISK_LABELS[tier]}
      aria-label={RISK_LABELS[tier]}
    >
      {tier === "hard_limit" ? "Hard limit" : RISK_LABELS[tier]}
    </span>
  );
}
