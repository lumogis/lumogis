// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "../../api/client";
import { MIN_PASSWORD_LENGTH, changeMyPassword } from "../../api/passwordManagement";
import { useAuth, useUser } from "../../auth/AuthProvider";

export function MeProfileView(): JSX.Element {
  const u = useUser();
  const { client, logout } = useAuth();
  const [formOpen, setFormOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const pwMut = useMutation({
    mutationFn: async () => {
      setLocalError(null);
      if (newPassword.length < MIN_PASSWORD_LENGTH) {
        throw new Error(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      }
      if (newPassword !== confirmPassword) {
        throw new Error("New password and confirmation do not match.");
      }
      if (newPassword === currentPassword) {
        throw new Error("New password must differ from the current password.");
      }
      await changeMyPassword(client, {
        currentPassword,
        newPassword,
      });
    },
    onSuccess: async () => {
      try {
        sessionStorage.setItem(
          "lumogis_login_flash",
          "Password changed. Please sign in again.",
        );
      } catch {
        /* ignore quota / privacy mode */
      }
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setFormOpen(false);
      await logout();
    },
    onError: (e: unknown) => {
      if (e instanceof ApiError) {
        setLocalError(e.detail);
        return;
      }
      if (e instanceof Error) {
        setLocalError(e.message);
        return;
      }
      setLocalError("Request failed.");
    },
  });

  if (!u) return <p>Not signed in.</p>;
  return (
    <section>
      <h2>Profile</h2>
      <dl>
        <dt>Email</dt>
        <dd>{u.email}</dd>
        <dt>Role</dt>
        <dd>{u.role}</dd>
        <dt>User id</dt>
        <dd>
          <code>{u.id}</code>
        </dd>
      </dl>
      <p>
        <button type="button" onClick={() => setFormOpen((v) => !v)}>
          {formOpen ? "Cancel" : "Change password"}
        </button>
      </p>
      {formOpen && (
        <form
          style={{ maxWidth: "24rem", display: "grid", gap: "0.5rem" }}
          onSubmit={(e) => {
            e.preventDefault();
            pwMut.mutate();
          }}
        >
          <label>
            Current password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
          </label>
          <label>
            New password (min {MIN_PASSWORD_LENGTH})
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </label>
          <label>
            Confirm new password
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
          </label>
          {localError && (
            <p role="alert" style={{ color: "var(--lumogis-danger, #f66)" }}>
              {localError}
            </p>
          )}
          <button type="submit" disabled={pwMut.isPending}>
            {pwMut.isPending ? "Saving…" : "Save new password"}
          </button>
        </form>
      )}
      <p style={{ fontSize: "0.85rem", maxWidth: "32rem" }}>
        Forgot your password? Ask a household admin to reset it from Admin → Users, or if you have shell
        access, from the <code>orchestrator/</code> directory run{" "}
        <code>python -m scripts.reset_password</code> (see script docstring).
      </p>
    </section>
  );
}
