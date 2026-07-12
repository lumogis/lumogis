// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useAuth, useUser } from "../auth/AuthProvider";

export function ProfileMenuButton(): JSX.Element | null {
  const user = useUser();
  const { logout } = useAuth();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (!user) return null;

  const initial = (user.email?.[0] ?? "?").toUpperCase();

  return (
    <div className="lumogis-profile-menu" ref={rootRef}>
      <button
        type="button"
        className="lumogis-profile-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        aria-label="Account menu"
      >
        <span className="lumogis-profile-menu__avatar" aria-hidden>
          {initial}
        </span>
      </button>
      {open ? (
        <div className="lumogis-profile-menu__panel" role="menu">
          <div className="lumogis-profile-menu__head">
            <span className="lumogis-profile-menu__email">{user.email}</span>
            <span className="lumogis-profile-menu__role">{user.role}</span>
          </div>
          <Link to="/me/profile" className="lumogis-profile-menu__item" role="menuitem" onClick={() => setOpen(false)}>
            Profile
          </Link>
          <Link to="/me" className="lumogis-profile-menu__item" role="menuitem" onClick={() => setOpen(false)}>
            Settings
          </Link>
          {user.role === "admin" ? (
            <Link to="/admin" className="lumogis-profile-menu__item" role="menuitem" onClick={() => setOpen(false)}>
              Admin
            </Link>
          ) : null}
          <button
            type="button"
            className="lumogis-profile-menu__item lumogis-profile-menu__item--danger"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void logout();
            }}
          >
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
