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
    <div className={["flex min-w-0 items-center gap-2 text-xs text-muted-foreground", className].filter(Boolean).join(" ")}>
      <code className="min-w-0 truncate font-mono" title={value}>
        {middleEllipsis(value)}
      </code>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="h-auto shrink-0 px-1.5 py-0.5 text-xs"
        onClick={() => void onCopy()}
        aria-label={`${label}: ${value}`}
      >
        {copied ? "Copied" : label}
      </Button>
    </div>
  );
}
