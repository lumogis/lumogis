// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Modal overlay with Tab focus trap, ESC close, and ``#root`` inert while open (LUM-165).

import { useCallback, useEffect, useRef, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface ModalFrameProps {
  open: boolean;
  /** ``aria-labelledby`` target — must exist inside ``children``. */
  titleId: string;
  children: ReactNode;
  onClose: () => void;
}

function listFocusables(root: HTMLElement): HTMLElement[] {
  const sel =
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const raw = Array.from(root.querySelectorAll<HTMLElement>(sel));
  const filtered = raw.filter((el) => {
    const style = window.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    return el.offsetParent !== null || el.getClientRects().length > 0;
  });
  // jsdom often reports `offsetParent === null` for fixed/portal content — fall back to raw.
  return filtered.length > 0 ? filtered : raw;
}

export function ModalFrame({ open, titleId, children, onClose }: ModalFrameProps): JSX.Element | null {
  const shellRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;

    const root = document.getElementById("root");
    const prev = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    if (root) {
      root.setAttribute("inert", "");
      root.setAttribute("aria-hidden", "true");
    }

    const raf = window.requestAnimationFrame(() => {
      const shell = shellRef.current;
      if (!shell) return;
      const list = listFocusables(shell);
      (list[0] ?? shell).focus();
    });

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const shell = shellRef.current;
      if (!shell) return;
      const list = listFocusables(shell);
      if (list.length === 0) {
        e.preventDefault();
        return;
      }
      const first = list[0]!;
      const last = list[list.length - 1]!;
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);

    return () => {
      window.cancelAnimationFrame(raf);
      document.removeEventListener("keydown", onKeyDown, true);
      if (root) {
        root.removeAttribute("inert");
        root.removeAttribute("aria-hidden");
      }
      if (prev?.isConnected) prev.focus();
    };
  }, [open]);

  const stopMouseDown = useCallback((e: MouseEvent) => {
    e.stopPropagation();
  }, []);

  if (!open) return null;

  const overlay = (
    <div
      className="lumogis-onboarding-overlay"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        display: "grid",
        placeItems: "center",
        zIndex: 2000,
      }}
      role="presentation"
      onMouseDown={stopMouseDown}
    >
      <div
        ref={shellRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={{
          outline: "none",
          background: "var(--lumogis-surface, #1a1a1a)",
          color: "var(--lumogis-fg, #eee)",
          padding: "1.25rem",
          maxWidth: "min(28rem, 100vw - 2rem)",
          borderRadius: "8px",
          border: "1px solid var(--lumogis-border, #333)",
        }}
      >
        {children}
      </div>
    </div>
  );

  return createPortal(overlay, document.body);
}
