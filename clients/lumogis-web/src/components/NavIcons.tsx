// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

/* eslint-disable react-refresh/only-export-components -- nav icon helpers co-located with NavIcon. */

export type NavIconKey =
  | "chat"
  | "search"
  | "documents"
  | "capture"
  | "approvals"
  | "me"
  | "admin";

const PATHS: Record<NavIconKey, string> = {
  chat: "M4 6h16v11H4zm2 2v7h12V8zm3 9h6",
  search: "M10 4a6 6 0 1 1 0 12 6 6 0 0 1 0-12zm8 14-4.2-4.2",
  documents: "M6 4h8l4 4v13H6zm2 2v13h10V9h-4V6zm2 0h2v3h-2",
  capture: "M12 3v3m-4 2v2m8-2v2M5 10h14v9H5zm3 3h8",
  approvals: "M9 11l2 2 4-4M7 4h10v16H7z",
  me: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-6 9a6 6 0 0 1 12 0",
  admin: "M12 8v4m0 4h.01M4 6h16v12H4z",
};

export interface NavIconProps {
  name: NavIconKey;
  size?: number;
  className?: string;
}

export function NavIcon({ name, size = 19, className }: NavIconProps): JSX.Element {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

export function navIconKeyForItem(key: string): NavIconKey | null {
  if (key === "chat" || key === "search" || key === "documents" || key === "capture" || key === "approvals" || key === "me" || key === "admin") {
    return key;
  }
  return null;
}

export function isSecondaryNavKey(key: string): boolean {
  return key === "me" || key === "admin";
}
