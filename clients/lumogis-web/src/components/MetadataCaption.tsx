// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useCallback, useState } from "react";

import { Button } from "./Button";
import { middleEllipsis } from "../util/middleEllipsis";

export interface MetadataCaptionProps {
  value: string;
  label?: string;
  className?: string;
}

export function MetadataCaption({
  value,
  label = "Copy",
  className,
}: MetadataCaptionProps): JSX.Element {
  const [copied, setCopied] = useState(false);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — silent */
    }
  }, [value]);

  return (
    <div className={["lumogis-metadata-caption", className].filter(Boolean).join(" ")}>
      <code className="lumogis-metadata-caption__text" title={value}>
        {middleEllipsis(value)}
      </code>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="lumogis-metadata-caption__copy-btn"
        onClick={() => void onCopy()}
        aria-label={`${label}: ${value}`}
      >
        {copied ? "Copied" : label}
      </Button>
    </div>
  );
}
