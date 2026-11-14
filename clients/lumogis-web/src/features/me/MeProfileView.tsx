// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "../../api/client";
import type { McpTokenRow } from "../../api/mcpTokens";
import { MIN_PASSWORD_LENGTH, changeMyPassword } from "../../api/passwordManagement";
import { useAuth, useUser } from "../../auth/AuthProvider";
import { MetadataCaption } from "../../components/MetadataCaption";
import { Button } from "../../components/Button";
import { TURN_ON_SIGN_IN_DOC_URL } from "../../components/SingleUserBanner";
import { selfPasswordConsequence } from "./mcpTokenDisplay";

export function MeProfileView(): JSX.Element {
  const u = useUser();
  const { client, logout, singleUser } = useAuth();
  const [formOpen, setFormOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const qc = useQueryClient();

  // LUM-885: a password change revokes the member's MCP tokens. Same key and URL as
  // MeMcpTokensView, so the cache is shared; the endpoint lists active tokens only.
  const tokensQ = useQuery({
    queryKey: ["mcp", "me"],
    queryFn: () => client.getJson<McpTokenRow[]>("/api/v1/me/mcp-tokens"),
    enabled: formOpen,
  });
  const activeTokens = tokensQ.isSuccess ? tokensQ.data.length : null;

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
      void qc.invalidateQueries({ queryKey: ["mcp", "me"] });
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
      <dl className="lumogis-kv-list">
        <div className="lumogis-kv-row">
          <dt className="lumogis-kv-row__label">Email</dt>
          <dd className="lumogis-kv-row__value">{singleUser ? "None — sign-in is off" : u.email}</dd>
        </div>
        <div className="lumogis-kv-row">
          <dt className="lumogis-kv-row__label">Role</dt>
          <dd className="lumogis-kv-row__value">{u.role}</dd>
        </div>
        <div className="lumogis-kv-row">
          <dt className="lumogis-kv-row__label">User id</dt>
          <dd className="lumogis-kv-row__value">
            <MetadataCaption value={u.id} label="Copy" />
          </dd>
        </div>
      </dl>
      {singleUser ? (
        // LUM-853: the route answers 503 with sign-in off, so there is nothing to offer.
        <p>
          Sign-in is off on this Lumogis, so this account has no password.{" "}
          <a href={TURN_ON_SIGN_IN_DOC_URL} target="_blank" rel="noopener noreferrer">
            How to turn on sign-in
          </a>
        </p>
      ) : (
        <p>
          <Button type="button" variant="secondary" onClick={() => setFormOpen((v) => !v)}>
            {formOpen ? "Cancel" : "Change password"}
          </Button>
        </p>
      )}
      {formOpen && !singleUser && (
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
          <p className="lumogis-field__hint" data-testid="password-change-consequence">
            {selfPasswordConsequence(activeTokens)}
          </p>
          {localError && (
            <p role="alert" style={{ color: "var(--lumogis-danger, #f66)" }}>
              {localError}
            </p>
          )}
          <Button type="submit" variant="primary" disabled={pwMut.isPending}>
            {pwMut.isPending ? "Saving…" : "Save new password"}
          </Button>
        </form>
      )}
      {singleUser ? null : (
        <p className="lumogis-prose-mono" style={{ fontSize: "0.85rem" }}>
          Forgot your password? Ask a household admin to reset it from Admin → Users, or if you have shell
          access, from the <code>orchestrator/</code> directory run{" "}
          <code>python -m scripts.reset_password</code> (see script docstring).
        </p>
      )}
    </section>
  );
}
