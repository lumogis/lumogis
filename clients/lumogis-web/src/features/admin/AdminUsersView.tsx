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
import type { UserRow } from "../_shared/UserPicker";

interface UserAdminView extends UserRow {
  created_at: string;
  last_login_at: string | null;
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

export function AdminUsersView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [nEmail, setNEmail] = useState("");
  const [nPass, setNPass] = useState("");
  const [nRole, setNRole] = useState<"admin" | "user">("user");
  const [resetFor, setResetFor] = useState<UserAdminView | null>(null);
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

  const listQ = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => client.getJson<UserAdminView[]>("/api/v1/admin/users"),
  });

  const archivesQ = useQuery({
    queryKey: ["admin", "user-imports"],
    queryFn: () => listUserImportArchives(client),
    enabled: importOpen,
  });

  const activeAdmins = useMemo(
    () => listQ.data?.filter((u) => u.role === "admin" && !u.disabled) ?? [],
    [listQ.data],
  );
  const isLastActiveAdmin = (u: UserAdminView) =>
    u.role === "admin" && !u.disabled && activeAdmins.length === 1 && activeAdmins[0]?.id === u.id;

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
    mutationFn: ({ id, body }: { id: string; body: { role?: "admin" | "user"; disabled?: boolean } }) =>
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
      {msg && <p role="status">{msg}</p>}
      <div className="lumogis-dense-actions">
        <button type="button" onClick={() => setCreateOpen(true)}>
          Create user
        </button>
        <button type="button" onClick={openImportModal}>
          Import from backup
        </button>
      </div>
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
                <option value="user">user</option>
                <option value="admin">admin</option>
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
                <option value="user">user</option>
                <option value="admin">admin</option>
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
      <div className="lumogis-table-scroll">
        <table className="lumogis-dense-table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Disabled</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {listQ.data?.map((u) => {
              const lastAdmin = isLastActiveAdmin(u);
              return (
                <tr key={u.id}>
                  <td className="lumogis-long-text">{u.email}</td>
                  <td>{u.role}</td>
                  <td>{u.disabled ? "yes" : "no"}</td>
                  <td>
                    <div className="lumogis-dense-actions lumogis-dense-actions--stack">
                      <button
                        type="button"
                        title={lastAdmin ? "Cannot remove the last active admin." : undefined}
                        disabled={lastAdmin}
                        onClick={() => {
                          setMsg(null);
                          patchM.mutate({
                            id: u.id,
                            body: { role: u.role === "admin" ? "user" : "admin" },
                          });
                        }}
                      >
                        Make {u.role === "admin" ? "user" : "admin"}
                      </button>
                      <button
                        type="button"
                        title={lastAdmin ? "Cannot remove the last active admin." : undefined}
                        disabled={lastAdmin || u.disabled}
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
                        disabled={exportingId === u.id}
                        onClick={() => void runExportBackup(u)}
                      >
                        {exportingId === u.id ? "Exporting…" : "Export backup"}
                      </button>
                      <button
                        type="button"
                        title={lastAdmin ? "Cannot remove the last active admin." : undefined}
                        disabled={lastAdmin}
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
