// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

export interface CopyOnceModalProps {
  open: boolean;
  title: string;
  plaintext: string;
  onClose: () => void;
  extraBanner?: ReactNode;
}

export function CopyOnceModal({ open, title, plaintext, onClose, extraBanner }: CopyOnceModalProps): JSX.Element | null {
  const [copied, setCopied] = useState(false);
  const copyBtnRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) setCopied(false);
  }, [open]);

  useEffect(() => {
    if (!open) return;

    const prev =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const id = window.requestAnimationFrame(() => {
      copyBtnRef.current?.focus();
    });

    const onDocKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
      }
    };
    document.addEventListener("keydown", onDocKeyDown);

    return () => {
      window.cancelAnimationFrame(id);
      document.removeEventListener("keydown", onDocKeyDown);
      if (prev?.isConnected) prev.focus();
    };
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

  if (!open) return null;

  return (
    <div
      className="lumogis-modal"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.45)",
        display: "grid",
        placeItems: "center",
        zIndex: 1000,
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="copy-once-title"
    >
      <div
        style={{
          background: "var(--lumogis-surface, #1a1a1a)",
          color: "var(--lumogis-fg, #eee)",
          padding: "1.25rem",
          maxWidth: "min(32rem, 100vw - 2rem)",
          borderRadius: "8px",
        }}
      >
        <h2 id="copy-once-title" style={{ marginTop: 0 }}>
          {title}
        </h2>
        {extraBanner}
        <p style={{ fontSize: "0.9rem" }}>Save this now — it will not be shown again.</p>
        <pre
          style={{
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
            padding: "0.75rem",
            background: "rgba(0,0,0,0.3)",
            borderRadius: 4,
            fontSize: "0.85rem",
        }}
        >
          {plaintext}
        </pre>
        <p style={{ fontSize: "0.8rem", opacity: 0.9 }}>
          Paste the value into your password manager and clear your clipboard.
        </p>
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
          <button ref={copyBtnRef} type="button" onClick={onCopy}>
            {copied ? "Copied" : "Copy to clipboard"}
          </button>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
