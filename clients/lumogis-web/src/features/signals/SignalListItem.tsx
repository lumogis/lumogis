// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import type { EmailSignalRow } from "../../api/emailSources";
import { formatCategoryLabel, shouldShowCategoryChip } from "./categoryLabels";

export function SignalListItem({ signal }: { signal: EmailSignalRow }): JSX.Element {
  return (
    <li className="lumogis-email-signal-item" data-testid={`signal-${signal.signal_id}`}>
      <span className="lumogis-email-signal-item__title">{signal.title}</span>
      {shouldShowCategoryChip(signal.category_label) ? (
        <span className="lumogis-email-signal-item__chip" data-testid="category-chip">
          {formatCategoryLabel(signal.category_label)}
        </span>
      ) : null}
    </li>
  );
}
