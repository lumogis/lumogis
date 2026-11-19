// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Shows a secret once (a new key, a generated password) with a Copy button.
// Rebuilt on the shared Dialog in LUM-993 plan B Pass 5; props unchanged so
// callers keep working. Base UI returns focus to whatever opened it.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

export interface CopyOnceModalProps {
  open: boolean;
  title: string;
  plaintext: string;
  onClose: () => void;
  extraBanner?: ReactNode;
}

export function CopyOnceModal({ open, title, plaintext, onClose, extraBanner }: CopyOnceModalProps): JSX.Element {
  const [copied, setCopied] = useState(false);
  const copyBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) setCopied(false);
  }, [open]);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(plaintext);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  }, [plaintext]);

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent showCloseButton={false} initialFocus={copyBtnRef} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>Save this now. It will not be shown again.</DialogDescription>
        </DialogHeader>
        {extraBanner}
        <pre className="m-0 rounded-md bg-muted p-3 font-mono text-sm break-all whitespace-pre-wrap">{plaintext}</pre>
        <p className="m-0 text-xs text-muted-foreground">
          Paste it into your password manager, then clear your clipboard.
        </p>
        <DialogFooter showCloseButton>
          <Button ref={copyBtnRef} type="button" onClick={() => void onCopy()}>
            {copied ? "Copied" : "Copy to clipboard"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
