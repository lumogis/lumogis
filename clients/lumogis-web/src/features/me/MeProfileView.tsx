// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { ApiError } from "../../api/client";
import type { McpTokenRow } from "../../api/mcpTokens";
import { MIN_PASSWORD_LENGTH, changeMyPassword } from "../../api/passwordManagement";
import { useAuth, useUser } from "../../auth/AuthProvider";
import { MetadataCaption } from "../../components/MetadataCaption";
import { PageSection, Row } from "@/components/page/PageSection";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { help } from "@/copy/help";
import { Label } from "@/components/ui/label";
import { selfPasswordConsequence } from "./mcpTokenDisplay";

export function MeProfileView(): JSX.Element {
  const u = useUser();
  const { client, logout } = useAuth();
  const [formOpen, setFormOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const qc = useQueryClient();
  const ids = { current: useId(), next: useId(), confirm: useId() };

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
      <PageSection title="Account">
        <dl className="m-0">
          <Row label="Email">{u.email}</Row>
          <Row
            label="Role"
            info={help.profile.role}
          >
            <Badge variant="secondary">{u.role === "admin" ? "Admin" : "Member"}</Badge>
          </Row>
        </dl>
        <details className="mt-3 text-sm text-muted-foreground">
          <summary className="cursor-pointer select-none">Technical details</summary>
          <div className="mt-2 flex items-center gap-2">
            User id <MetadataCaption value={u.id} label="Copy" />
          </div>
        </details>
      </PageSection>

      <PageSection
        title="Password"
        description={help.profile.password}
      >
        {!formOpen ? (
          <Button type="button" variant="outline" size="lg" onClick={() => setFormOpen(true)}>
            Change password
          </Button>
        ) : (
          <form
            className="grid max-w-sm gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              pwMut.mutate();
            }}
          >
            <div className="grid gap-2">
              <Label htmlFor={ids.current}>Current password</Label>
              <Input
                id={ids.current}
                type="password"
                autoComplete="current-password"
                required
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor={ids.next}>New password</Label>
              <Input
                id={ids.next}
                type="password"
                autoComplete="new-password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                aria-describedby={`${ids.next}-hint`}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
              <p id={`${ids.next}-hint`} className="m-0 text-xs text-muted-foreground">
                At least {MIN_PASSWORD_LENGTH} characters.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor={ids.confirm}>Confirm new password</Label>
              <Input
                id={ids.confirm}
                type="password"
                autoComplete="new-password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
            </div>
            <p className="m-0 text-sm text-muted-foreground" data-testid="password-change-consequence">
              {selfPasswordConsequence(activeTokens)}
            </p>
            {localError && (
              <p role="alert" className="m-0 text-sm text-destructive">
                {localError}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button type="submit" size="lg" disabled={pwMut.isPending}>
                {pwMut.isPending ? "Saving…" : "Save new password"}
              </Button>
              <Button type="button" variant="ghost" size="lg" onClick={() => setFormOpen(false)}>
                Cancel
              </Button>
            </div>
          </form>
        )}
      </PageSection>
    </section>
  );
}
