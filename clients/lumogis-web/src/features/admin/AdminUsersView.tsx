// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import {
  archivePathForInventoryEntry,
  downloadAdminUserExportZip,
  listUserImportArchives,
  postUserImport,
  type ArchiveInventoryEntry,
  type ImportPlan,
  type ImportReceipt,
} from "../../api/adminUserImports";
import { MIN_PASSWORD_LENGTH, adminSetUserPassword } from "../../api/passwordManagement";
import {
  listAdminInvites,
  mintAdminInvite,
  revokeAdminInvite,
  type InviteAdminRow,
} from "../../api/invites";
import type { UserRow } from "../_shared/UserPicker";
import { formatLastActive, roleLabel } from "./adminUsersDisplay";

interface UserAdminView extends UserRow {
  created_at: string;
  last_login_at: string | null;
  last_seen_at: string | null; // LUM-334/520 — last authenticated request (throttled)
  display_name: string | null; // LUM-585 — admin-managed attribution label
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return "Request failed";
}

function safeExportedUserSummary(plan: ImportPlan): { email?: string; role?: string } {
  const u = plan.exported_user;
  if (!u || typeof u !== "object") return {};
  const o = u as Record<string, unknown>;
  return {
    email: typeof o.email === "string" ? o.email : undefined,
    role: typeof o.role === "string" ? o.role : undefined,
  };
}

/** "2 members · 1 admin" — singular/plural aware. */
function pluralCount(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

export function AdminUsersView(): JSX.Element {
  const { client, user } = useAuth();
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [nEmail, setNEmail] = useState("");
  const [nPass, setNPass] = useState("");
  const [nRole, setNRole] = useState<"admin" | "user">("user");
  const [resetFor, setResetFor] = useState<UserAdminView | null>(null);
  const [nameFor, setNameFor] = useState<UserAdminView | null>(null); // LUM-585
  const [nameInput, setNameInput] = useState("");
  const [resetPass, setResetPass] = useState("");
  const [resetConfirm, setResetConfirm] = useState("");

  const [importOpen, setImportOpen] = useState(false);
  const [importDryRun, setImportDryRun] = useState(true);
  const [importArchiveIdx, setImportArchiveIdx] = useState<number | null>(null);
  const [impEmail, setImpEmail] = useState("");
  const [impPass, setImpPass] = useState("");
  const [impRole, setImpRole] = useState<"admin" | "user">("user");
  const [importDialogMsg, setImportDialogMsg] = useState<string | null>(null);
  const [lastPlan, setLastPlan] = useState<ImportPlan | null>(null);
  const [lastReceipt, setLastReceipt] = useState<ImportReceipt | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteRole, setInviteRole] = useState<"admin" | "user">("user");
  // LUM-577 — per-user shared-scope gate stamped on the invite. Admins always
  // see the shared household union regardless, so the choice only applies to members.
  const [inviteAllowsShared, setInviteAllowsShared] = useState(true);
  const [lastInviteUrl, setLastInviteUrl] = useState<string | null>(null);
  const [lastInviteToken, setLastInviteToken] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => client.getJson<UserAdminView[]>("/api/v1/admin/users"),
  });

  const archivesQ = useQuery({
    queryKey: ["admin", "user-imports"],
    queryFn: () => listUserImportArchives(client),
    enabled: importOpen,
  });

  const invitesQ = useQuery({
    queryKey: ["admin", "invites"],
    queryFn: () => listAdminInvites(client),
  });

  const activeAdmins = useMemo(
    () => listQ.data?.filter((u) => u.role === "admin" && !u.disabled) ?? [],
    [listQ.data],
  );
  const isLastActiveAdmin = (u: UserAdminView) =>
    u.role === "admin" && !u.disabled && activeAdmins.length === 1 && activeAdmins[0]?.id === u.id;
  // The signed-in admin can't disable or delete their own account (backend returns 400 —
  // "ask another admin"); surface that as disabled controls instead of a raw error.
  const isSelf = (u: UserAdminView) => user != null && user.id === u.id;

  const memberSummary = useMemo(() => {
    const rows = listQ.data ?? [];
    const admins = rows.filter((u) => u.role === "admin").length;
    // Everything that isn't an admin counts as a member (the wire role `user`, surfaced
    // as "Member"); a future `guest` role would land here until it gets its own count.
    const members = rows.length - admins;
    return `${pluralCount(members, "member")} · ${pluralCount(admins, "admin")}`;
  }, [listQ.data]);

  const createM = useMutation({
    mutationFn: () =>
      client.postJson<{ email: string; password: string; role: "admin" | "user" }, UserAdminView>(
        "/api/v1/admin/users",
        { email: nEmail.trim(), password: nPass, role: nRole },
      ),
    onSuccess: () => {
      setCreateOpen(false);
      setNEmail("");
      setNPass("");
      setNRole("user");
      setMsg("User created.");
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (e) => {
      setMsg(errMsg(e));
    },
  });

  const patchM = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: { role?: "admin" | "user"; disabled?: boolean; display_name?: string | null };
    }) =>
      client.patchJson<typeof body, UserAdminView>(`/api/v1/admin/users/${encodeURIComponent(id)}`, body),
    onSuccess: () => {
      setMsg("Saved.");
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (e) => {
      setMsg(errMsg(e));
    },
  });

  const resetPwM = useMutation({
    mutationFn: () => {
      if (!resetFor) throw new Error("no user");
      if (resetPass.length < MIN_PASSWORD_LENGTH) {
        throw new Error(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      }
      if (resetPass !== resetConfirm) {
        throw new Error("Password and confirmation do not match.");
      }
      return adminSetUserPassword(client, resetFor.id, { newPassword: resetPass });
    },
    onSuccess: () => {
      setResetFor(null);
      setResetPass("");
      setResetConfirm("");
      setMsg("Password updated for user.");
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (e) => {
      setMsg(errMsg(e));
    },
  });

  const delM = useMutation({
    mutationFn: (id: string) => client.delete(`/api/v1/admin/users/${encodeURIComponent(id)}`),
    onSuccess: () => {
      setMsg("User deleted.");
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (e) => {
      setMsg(errMsg(e));
    },
  });

  const inviteMintM = useMutation({
    // Admins always resolve to allows_shared=true server-side; send true for them
    // so the stored invite metadata matches the effective grant.
    mutationFn: () =>
      mintAdminInvite(client, {
        role: inviteRole,
        allows_shared: inviteRole === "admin" ? true : inviteAllowsShared,
      }),
    onSuccess: (res) => {
      setLastInviteUrl(res.invite_url);
      setLastInviteToken(res.token);
      setMsg("Invite link created — copy it now; it is shown only once.");
      void qc.invalidateQueries({ queryKey: ["admin", "invites"] });
    },
    onError: (e) => {
      setMsg(errMsg(e));
    },
  });

  const inviteRevokeM = useMutation({
    mutationFn: (id: string) => revokeAdminInvite(client, id),
    onSuccess: () => {
      setMsg("Invite revoked.");
      void qc.invalidateQueries({ queryKey: ["admin", "invites"] });
    },
    onError: (e) => {
      setMsg(errMsg(e));
    },
  });

  function inviteStatus(row: InviteAdminRow): string {
    if (row.used_at) return "Redeemed";
    if (row.revoked_at) return "Revoked";
    if (new Date(row.expires_at).getTime() <= Date.now()) return "Expired";
    return "Active";
  }

  async function copyInviteLink(): Promise<void> {
    const text = lastInviteUrl ?? "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setMsg("Invite link copied to clipboard.");
    } catch {
      setMsg("Could not copy automatically — select and copy the link below.");
    }
  }

  function openInviteModal(): void {
    setInviteOpen(true);
    setInviteRole("user");
    setInviteAllowsShared(true);
    setLastInviteUrl(null);
    setLastInviteToken(null);
  }

  const importM = useMutation({
    mutationFn: async () => {
      const archives = archivesQ.data;
      if (importArchiveIdx === null || !archives || !archives[importArchiveIdx]) {
        throw new Error("Select a backup archive.");
      }
      const entry = archives[importArchiveIdx] as ArchiveInventoryEntry;
      if (!impEmail.trim()) throw new Error("Email is required.");
      if (impPass.length < MIN_PASSWORD_LENGTH) {
        throw new Error(`New account password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      }
      const archive_path = archivePathForInventoryEntry(entry);
      return postUserImport(client, {
        archive_path,
        dry_run: importDryRun,
        new_user: { email: impEmail.trim(), password: impPass, role: impRole },
      });
    },
    onSuccess: (result) => {
      setImportDialogMsg(null);
      if (result.kind === "plan") {
        setLastPlan(result.plan);
        setLastReceipt(null);
        setImportDialogMsg(
          result.plan.would_succeed
            ? "Preview: import would succeed. Uncheck “Preview only” to create the account."
            : "Preview: import would be refused — see details below.",
        );
        return;
      }
      setLastPlan(null);
      setLastReceipt(result.receipt);
      setImpPass("");
      setImportDialogMsg(
        `Import complete. New user id: ${result.receipt.new_user_id}. Initial password was set from this form only — it is not shown again.`,
      );
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
      void qc.invalidateQueries({ queryKey: ["admin", "user-imports"] });
    },
    onError: (e) => {
      setImportDialogMsg(errMsg(e));
    },
  });

  async function runExportBackup(u: UserAdminView): Promise<void> {
    setExportingId(u.id);
    setMsg(null);
    let objectUrl: string | null = null;
    try {
      const { blob, filename } = await downloadAdminUserExportZip(client, u.id);
      objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      a.click();
      setMsg(`Download started for ${u.email}.`);
    } catch (e) {
      setMsg(errMsg(e));
    } finally {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      setExportingId(null);
    }
  }

  function openImportModal(): void {
    setImportOpen(true);
    setImportDryRun(true);
    setImportArchiveIdx(null);
    setImpEmail("");
    setImpPass("");
    setImpRole("user");
    setImportDialogMsg(null);
    setLastPlan(null);
    setLastReceipt(null);
  }

  if (listQ.isPending) return <p>Loading…</p>;
  if (listQ.isError) return <p>Failed to load users.</p>;

  return (
    <section className="lumogis-admin-dense-section">
      <h2>Users</h2>
      <p className="lumogis-admin-user-counts" style={{ margin: "0 0 0.5rem", opacity: 0.8 }}>
        {memberSummary}
      </p>
      {msg && <p role="status">{msg}</p>}
      <div className="lumogis-dense-actions">
        <button type="button" onClick={() => setCreateOpen(true)}>
          Create user
        </button>
        <button type="button" onClick={openInviteModal}>
          Invite member
        </button>
        <button type="button" onClick={openImportModal}>
          Import from backup
        </button>
      </div>
      <p style={{ margin: "0.25rem 0 0", fontSize: "0.85rem", opacity: 0.8 }}>
        <strong>Create user</strong> sets an initial password you share manually.{" "}
        <strong>Invite member</strong> generates a single-use link (48 hours) for self-service
        signup.
      </p>
      {importOpen && (
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
        >
          <div
            className="lumogis-credential-form"
            style={{
              background: "var(--lumogis-surface, #1a1a1a)",
              padding: "1.25rem",
              borderRadius: 8,
              maxWidth: "min(40rem, 92vw)",
              minWidth: 0,
              maxHeight: "90vh",
              overflow: "auto",
            }}
          >
            <h3 style={{ marginTop: 0 }}>Import from backup</h3>
            <p style={{ margin: 0, fontSize: "0.9rem", opacity: 0.9 }}>
              Restores a per-user ZIP that already exists on the server under the export directory (listed below).
              This does not upload a file from your computer. The new account&apos;s initial password is only sent in
              this request and is not stored in the archive.
            </p>
            {importDialogMsg && <p role="status">{importDialogMsg}</p>}
            {archivesQ.isPending && <p>Loading archives…</p>}
            {archivesQ.isError && <p>Could not load backup inventory.</p>}
            {archivesQ.isSuccess && archivesQ.data.length === 0 && (
              <p>No export archives found on the server. Use “Export backup” on a user row first.</p>
            )}
            {archivesQ.isSuccess && archivesQ.data.length > 0 && (
              <label style={{ display: "grid", gap: "0.25rem" }}>
                Backup archive
                <select
                  value={importArchiveIdx === null ? "" : String(importArchiveIdx)}
                  onChange={(e) => {
                    const v = e.target.value;
                    setImportArchiveIdx(v === "" ? null : Number(v));
                    setLastPlan(null);
                    setLastReceipt(null);
                  }}
                >
                  <option value="">— Select —</option>
                  {archivesQ.data.map((a, i) => (
                    <option key={`${a.user_id}/${a.archive_filename}`} value={String(i)}>
                      {a.user_id}/{a.archive_filename} ({a.manifest_status}
                      {a.exported_user_email ? `; was ${a.exported_user_email}` : ""})
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label style={{ display: "grid", gap: "0.25rem" }}>
              New account email
              <input type="email" required value={impEmail} onChange={(e) => setImpEmail(e.target.value)} />
            </label>
            <label style={{ display: "grid", gap: "0.25rem" }}>
              New account password (min {MIN_PASSWORD_LENGTH})
              <input
                type="password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={impPass}
                onChange={(e) => setImpPass(e.target.value)}
                autoComplete="new-password"
              />
            </label>
            <label style={{ display: "grid", gap: "0.25rem" }}>
              Role
              <select value={impRole} onChange={(e) => setImpRole(e.target.value as "admin" | "user")}>
                <option value="user">Member</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <label style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <input
                type="checkbox"
                checked={importDryRun}
                onChange={(e) => {
                  setImportDryRun(e.target.checked);
                  setLastPlan(null);
                  setLastReceipt(null);
                }}
              />
              Preview only (dry run — no user created)
            </label>
            {lastPlan && (
              <div role="region" aria-label="Import preview">
                <p>
                  <strong>Would succeed:</strong> {lastPlan.would_succeed ? "yes" : "no"}
                </p>
                {(() => {
                  const su = safeExportedUserSummary(lastPlan);
                  if (su.email || su.role) {
                    return (
                      <p>
                        <strong>Exported user (from manifest):</strong>{" "}
                        {[su.email, su.role].filter(Boolean).join(" · ")}
                      </p>
                    );
                  }
                  return null;
                })()}
                {lastPlan.warnings.length > 0 && (
                  <div>
                    <strong>Warnings</strong>
                    <ul>
                      {lastPlan.warnings.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {lastPlan.missing_sections.length > 0 && (
                  <p>
                    <strong>Missing sections:</strong> {lastPlan.missing_sections.join(", ")}
                  </p>
                )}
              </div>
            )}
            {lastReceipt && (
              <div role="region" aria-label="Import result">
                <p>
                  <strong>New user id:</strong> {lastReceipt.new_user_id}
                </p>
                <p>
                  <strong>Sections imported:</strong> {lastReceipt.sections_imported.length}
                </p>
                {(lastReceipt.warnings?.length ?? 0) > 0 && (
                  <div>
                    <strong>Warnings</strong>
                    <ul>
                      {lastReceipt.warnings!.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
            <div className="lumogis-form-actions lumogis-form-actions--stack">
              <button
                type="button"
                disabled={importM.isPending || !archivesQ.data?.length}
                onClick={() => {
                  setImportDialogMsg(null);
                  importM.mutate();
                }}
              >
                {importDryRun ? "Run preview" : "Run import"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setImportOpen(false);
                }}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
      {resetFor && (
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
        >
          <form
            className="lumogis-credential-form"
            style={{
              background: "var(--lumogis-surface, #1a1a1a)",
              padding: "1.25rem",
              borderRadius: 8,
            }}
            onSubmit={(e) => {
              e.preventDefault();
              setMsg(null);
              resetPwM.mutate();
            }}
          >
            <h3 style={{ marginTop: 0 }}>Reset password — {resetFor.email}</h3>
            <label>
              New password (min {MIN_PASSWORD_LENGTH})
              <input
                type="password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={resetPass}
                onChange={(e) => setResetPass(e.target.value)}
              />
            </label>
            <label>
              Confirm password
              <input
                type="password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={resetConfirm}
                onChange={(e) => setResetConfirm(e.target.value)}
              />
            </label>
            <div className="lumogis-form-actions lumogis-form-actions--stack">
              <button type="submit">Save</button>
              <button
                type="button"
                onClick={() => {
                  setResetFor(null);
                  setResetPass("");
                  setResetConfirm("");
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
      {nameFor && (
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
        >
          <form
            className="lumogis-credential-form"
            style={{
              background: "var(--lumogis-surface, #1a1a1a)",
              padding: "1.25rem",
              borderRadius: 8,
            }}
            onSubmit={(e) => {
              e.preventDefault();
              setMsg(null);
              // Empty clears the name (→ NULL → email local-part attribution).
              patchM.mutate({ id: nameFor.id, body: { display_name: nameInput } });
              setNameFor(null);
            }}
          >
            <h3 style={{ marginTop: 0 }}>Display name — {nameFor.email}</h3>
            <label>
              Shown as &ldquo;Shared by …&rdquo; on this member&apos;s shared items. Leave
              empty to use their email name.
              <input
                type="text"
                maxLength={64}
                data-testid="display-name-input"
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
              />
            </label>
            <div className="lumogis-form-actions lumogis-form-actions--stack">
              <button type="submit">Save</button>
              <button type="button" onClick={() => setNameFor(null)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
      {createOpen && (
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
        >
          <form
            className="lumogis-credential-form"
            style={{
              background: "var(--lumogis-surface, #1a1a1a)",
              padding: "1.25rem",
              borderRadius: 8,
            }}
            onSubmit={(e) => {
              e.preventDefault();
              setMsg(null);
              createM.mutate();
            }}
          >
            <h3 style={{ marginTop: 0 }}>New user</h3>
            <label>
              Email
              <input type="email" required value={nEmail} onChange={(e) => setNEmail(e.target.value)} />
            </label>
            <label>
              Password (min 12)
              <input type="password" required minLength={12} value={nPass} onChange={(e) => setNPass(e.target.value)} />
            </label>
            <label>
              Role
              <select value={nRole} onChange={(e) => setNRole(e.target.value as "admin" | "user")}>
                <option value="user">Member</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <div className="lumogis-form-actions lumogis-form-actions--stack">
              <button type="submit">Create</button>
              <button type="button" onClick={() => setCreateOpen(false)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
      {inviteOpen && (
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
        >
          <div
            className="lumogis-credential-form"
            style={{
              background: "var(--lumogis-surface, #1a1a1a)",
              padding: "1.25rem",
              borderRadius: 8,
              maxWidth: "min(36rem, 92vw)",
            }}
          >
            <h3 style={{ marginTop: 0 }}>Invite member</h3>
            <p style={{ margin: 0, fontSize: "0.9rem", opacity: 0.9 }}>
              Generates a single-use link valid for 48 hours. Copy and share it securely — the full
              token is shown only once.
            </p>
            <label style={{ display: "grid", gap: "0.25rem", marginTop: "0.75rem" }}>
              Role for new member
              <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value as "admin" | "user")}>
                <option value="user">Member</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.75rem" }}>
              <input
                type="checkbox"
                checked={inviteRole === "admin" ? true : inviteAllowsShared}
                disabled={inviteRole === "admin"}
                onChange={(e) => setInviteAllowsShared(e.target.checked)}
              />
              Allow access to shared household memory
            </label>
            <p style={{ margin: "0.35rem 0 0", fontSize: "0.8rem", opacity: 0.75 }}>
              {inviteRole === "admin"
                ? "Admins always see the shared household memory."
                : "When off, this member sees only their own personal memory — shared household items stay hidden (system items remain visible)."}
            </p>
            {lastInviteUrl && (
              <div style={{ marginTop: "0.75rem" }}>
                <p style={{ margin: "0 0 0.35rem", fontSize: "0.85rem" }}>Invite link</p>
                <input readOnly value={lastInviteUrl} style={{ width: "100%" }} />
                {lastInviteToken ? (
                  <p style={{ fontSize: "0.8rem", opacity: 0.75, margin: "0.35rem 0 0" }}>
                    Token prefix: {lastInviteToken.slice(0, 12)}…
                  </p>
                ) : null}
              </div>
            )}
            <div className="lumogis-form-actions lumogis-form-actions--stack" style={{ marginTop: "1rem" }}>
              <button type="button" disabled={inviteMintM.isPending} onClick={() => inviteMintM.mutate()}>
                {lastInviteUrl ? "Generate another link" : "Generate invite link"}
              </button>
              {lastInviteUrl ? (
                <button type="button" onClick={() => void copyInviteLink()}>
                  Copy link
                </button>
              ) : null}
              <button type="button" onClick={() => setInviteOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="lumogis-table-scroll" style={{ marginBottom: "1rem" }}>
        <h3 style={{ margin: "0 0 0.5rem" }}>Invites</h3>
        {invitesQ.isPending && <p>Loading invites…</p>}
        {invitesQ.isError && <p>Could not load invites.</p>}
        {invitesQ.isSuccess && invitesQ.data.length === 0 && <p>No invites yet.</p>}
        {invitesQ.isSuccess && invitesQ.data.length > 0 && (
          <table className="lumogis-dense-table">
            <thead>
              <tr>
                <th>Role</th>
                <th>Shared access</th>
                <th>Status</th>
                <th>Expires</th>
                <th>Prefix</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {invitesQ.data.map((inv) => {
                const status = inviteStatus(inv);
                const canRevoke = status === "Active";
                return (
                  <tr key={inv.id}>
                    <td>{roleLabel(inv.role)}</td>
                    <td>{inv.role === "admin" || inv.allows_shared ? "Yes" : "No"}</td>
                    <td>{status}</td>
                    <td>{new Date(inv.expires_at).toLocaleString()}</td>
                    <td>{inv.token_prefix ?? "—"}</td>
                    <td>
                      <button
                        type="button"
                        disabled={!canRevoke || inviteRevokeM.isPending}
                        onClick={() => {
                          if (window.confirm("Revoke this invite before it is used?")) {
                            inviteRevokeM.mutate(inv.id);
                          }
                        }}
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <div className="lumogis-table-scroll">
        <table className="lumogis-dense-table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Role</th>
              <th>Last active</th>
              <th>Disabled</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {listQ.data?.map((u) => {
              const lastAdmin = isLastActiveAdmin(u);
              const self = isSelf(u);
              // Single source of truth for the role toggle so the button label and the
              // PATCH body can never drift apart.
              const willPromoteToAdmin = u.role !== "admin";
              // Reason priority mirrors the backend check order (last-admin guard is
              // evaluated before the self guard in admin_users.py), so the title stays
              // consistent with the error the API would return if the click went through.
              const disableTitle = lastAdmin
                ? "Cannot remove the last active admin."
                : self
                  ? "You can't disable your own account — ask another admin."
                  : undefined;
              const deleteTitle = lastAdmin
                ? "Cannot remove the last active admin."
                : self
                  ? "You can't delete your own account — ask another admin."
                  : undefined;
              return (
                <tr key={u.id}>
                  <td className="lumogis-long-text">{u.email}</td>
                  <td data-testid={`display-name-${u.id}`}>{u.display_name ?? "—"}</td>
                  <td>{roleLabel(u.role)}</td>
                  <td>{formatLastActive(u.last_seen_at)}</td>
                  <td>{u.disabled ? "yes" : "no"}</td>
                  <td>
                    <div className="lumogis-dense-actions lumogis-dense-actions--stack">
                      <button
                        type="button"
                        title={lastAdmin ? "Cannot remove the last active admin." : undefined}
                        disabled={lastAdmin}
                        onClick={() => {
                          const message = willPromoteToAdmin
                            ? `Make ${u.email} an Admin? Admins can manage every household member, including other admins.`
                            : `Change ${u.email} to Member? They will lose admin access to household management.`;
                          if (!window.confirm(message)) return;
                          setMsg(null);
                          patchM.mutate({
                            id: u.id,
                            body: { role: willPromoteToAdmin ? "admin" : "user" },
                          });
                        }}
                      >
                        Make {willPromoteToAdmin ? "Admin" : "Member"}
                      </button>
                      <button
                        type="button"
                        title={disableTitle}
                        disabled={lastAdmin || u.disabled || self}
                        onClick={() => {
                          setMsg(null);
                          patchM.mutate({ id: u.id, body: { disabled: !u.disabled } });
                        }}
                      >
                        {u.disabled ? "Enable" : "Disable"}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setMsg(null);
                          setResetPass("");
                          setResetConfirm("");
                          setResetFor(u);
                        }}
                      >
                        Reset password
                      </button>
                      <button
                        type="button"
                        data-testid={`set-name-${u.id}`}
                        onClick={() => {
                          setMsg(null);
                          setNameInput(u.display_name ?? "");
                          setNameFor(u);
                        }}
                      >
                        Set name
                      </button>
                      <button
                        type="button"
                        disabled={exportingId === u.id}
                        onClick={() => void runExportBackup(u)}
                      >
                        {exportingId === u.id ? "Exporting…" : "Export backup"}
                      </button>
                      <button
                        type="button"
                        title={deleteTitle}
                        disabled={lastAdmin || self}
                        onClick={() => {
                          if (window.confirm(`Delete ${u.email}?`)) {
                            setMsg(null);
                            delM.mutate(u.id);
                          }
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
