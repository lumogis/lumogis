// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Risk tier badge — parent plan §"Phase 1 Pass 1.4 item 12".
//
// Mapping (LUM-993 Pass 7, on the ui Badge): low -> quiet, medium -> outlined,
// high -> red (write + non-reversible), hard_limit -> quiet (cannot be elevated).
// The tier is exposed as `data-risk` for tests.

import type { RiskTier } from "../../api/approvals";
import { Badge } from "@/components/ui/badge";

const RISK_LABELS: Record<RiskTier, string> = {
  low: "Low risk",
  medium: "Medium risk",
  high: "High risk",
  hard_limit: "Hard limit",
};

const RISK_VARIANT: Record<RiskTier, "secondary" | "outline" | "destructive"> = {
  low: "secondary",
  medium: "outline",
  high: "destructive",
  hard_limit: "secondary",
};

export interface RiskBadgeProps {
  tier: RiskTier;
}

export function RiskBadge({ tier }: RiskBadgeProps): JSX.Element {
  return (
    <Badge
      variant={RISK_VARIANT[tier]}
      data-risk={tier}
      title={RISK_LABELS[tier]}
      aria-label={RISK_LABELS[tier]}
    >
      {RISK_LABELS[tier]}
    </Badge>
  );
}
