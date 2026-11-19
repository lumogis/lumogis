// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Members and invites. Rebuilt on the shared components in LUM-993 plan B Pass 6: the
// five hand-made modals are ui Dialog (forms) and AlertDialog (confirmations), members
// and invites are rows, and every API call, query key, invalidation and guard is as before.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import type { McpTokenAdminRow } from "../../api/mcpTokens";
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
  absoluteInviteUrl,
  isLoopbackHost,
  listAdminInvites,
  mintAdminInvite,
  revokeAdminInvite,
  type InviteAdminRow,
} from "../../api/invites";
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { RowActions, type RowAction } from "../../components/page/RowActions";
import { Alert, AlertDescription } from "../../components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { formatDateTime } from "../../util/formatTimestamp";
import type { UserRow } from "../_shared/UserPicker";
import { adminResetConsequence } from "../me/mcpTokenDisplay";
import { formatLastActive, roleLabel } from "./adminUsersDisplay";

interface UserAdminView extends UserRow {
  created_at: string;
  last_login_at: string | null;
  last_seen_at: string | null; // LUM-334/520 — last authenticated request (throttled)
  display_name: string | null; // LUM-585 — admin-managed attribution label
}

type Role = "admin" | "user";

// What the confirm dialog is asking about. One AlertDialog serves every destructive or
// sweeping action on this page.
type Confirm =
  | { kind: "role"; user: UserAdminView; toAdmin: boolean }
  | { kind: "disable"; user: UserAdminView }
  | { kind: "delete"; user: UserAdminView }
  | { kind: "revokeInvite"; invite: InviteAdminRow };

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

const ROLE_LABELS: Record<Role, string> = { user: "Member", admin: "Admin" };

/** Member / Admin choice, shared by the create, invite and import dialogs. */
function RoleSelect({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: Role;
  onChange: (r: Role) => void;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} onValueChange={(v) => onChange(v === "admin" ? "admin" : "user")}>
        <SelectTrigger id={id} aria-label={label} className="w-48">
          <SelectValue>{(v: string | null) => ROLE_LABELS[v === "admin" ? "admin" : "user"]}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="user">Member</SelectItem>
          <SelectItem value="admin">Admin</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

/** Inline failure inside a dialog (the page-level alert would sit behind the overlay). */
function FormError({ message }: { message: string | null }): JSX.Element | null {
  if (!message) return null;
  return (
    <Alert variant="destructive">
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}

/** Plain label for a backup file in the picker: whose it was, and anything wrong with it. */
function archiveLabel(a: ArchiveInventoryEntry): string {
  const who = a.exported_user_email ?? "Unknown member";
  const state = a.manifest_status === "valid" ? "" : " (needs checking)";
  return `${who} — ${a.archive_filename}${state}`;
}

export function AdminUsersView(): JSX.Element {
  const { client, user } = useAuth();
  const qc = useQueryClient();
  // Failures to read on the page itself; plain success is a toast. Failures inside a
  // dialog show in the dialog (`formErr`), where the person is looking.
  const [pageErr, setPageErr] = useState<string | null>(null);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [nEmail, setNEmail] = useState("");
  const [nPass, setNPass] = useState("");
  const [nRole, setNRole] = useState<Role>("user");
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
  const [impRole, setImpRole] = useState<Role>("user");
  const [importDialogMsg, setImportDialogMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);
  const [lastPlan, setLastPlan] = useState<ImportPlan | null>(null);
  const [lastReceipt, setLastReceipt] = useState<ImportReceipt | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteRole, setInviteRole] = useState<Role>("user");
  // LUM-577 — per-user shared-scope gate stamped on the invite. Admins always
  // see the shared household union regardless, so the choice only applies to members.
  const [inviteAllowsShared, setInviteAllowsShared] = useState(true);
  const [lastInviteUrl, setLastInviteUrl] = useState<string | null>(null);

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

  // LUM-885: resetting a password revokes the target's MCP tokens. Same key and URL as
  // AdminMcpTokensView (it lists revoked rows too), so count only the unrevoked ones.
  const resetTokensQ = useQuery({
    queryKey: ["mcp", "admin", resetFor?.id ?? ""],
    queryFn: () =>
      client.getJson<McpTokenAdminRow[]>(
        `/api/v1/admin/users/${encodeURIComponent(resetFor?.id ?? "")}/mcp-tokens`,
      ),
    enabled: Boolean(resetFor),
  });
  const resetActiveTokens = resetTokensQ.isSuccess
    ? resetTokensQ.data.filter((t) => t.revoked_at == null).length
    : null;

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
      client.postJson<{ email: string; password: string; role: Role }, UserAdminView>(
        "/api/v1/admin/users",
        { email: nEmail.trim(), password: nPass, role: nRole },
      ),
    onSuccess: () => {
      setCreateOpen(false);
      setNEmail("");
      setNPass("");
      setNRole("user");
      toast("User created.");
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (e) => {
      setFormErr(errMsg(e));
    },
  });

  const patchM = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: { role?: Role; disabled?: boolean; display_name?: string | null };
    }) =>
      client.patchJson<typeof body, UserAdminView>(`/api/v1/admin/users/${encodeURIComponent(id)}`, body),
    onSuccess: (_data, vars) => {
      toast("Saved.");
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
      if (vars.body.role !== undefined) {
        // A demotion revokes the user's MCP tokens and cancels the invites they sent (LUM-885).
        void qc.invalidateQueries({ queryKey: ["mcp", "admin", vars.id] });
        void qc.invalidateQueries({ queryKey: ["admin", "invites"] });
      }
    },
    onError: (e) => {
      setPageErr(errMsg(e));
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
      if (resetFor) void qc.invalidateQueries({ queryKey: ["mcp", "admin", resetFor.id] });
      setResetFor(null);
      setResetPass("");
      setResetConfirm("");
      toast("Password updated for user.");
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (e) => {
      setFormErr(errMsg(e));
    },
  });

  const delM = useMutation({
    mutationFn: (id: string) => client.delete(`/api/v1/admin/users/${encodeURIComponent(id)}`),
    onSuccess: (_data, id) => {
      toast("User deleted.");
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
      // Deleting revokes the user's MCP tokens and cancels the invites they sent (LUM-885).
      void qc.invalidateQueries({ queryKey: ["mcp", "admin", id] });
      void qc.invalidateQueries({ queryKey: ["admin", "invites"] });
    },
    onError: (e) => {
      setPageErr(errMsg(e));
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
      setLastInviteUrl(absoluteInviteUrl(res.invite_url));
      void qc.invalidateQueries({ queryKey: ["admin", "invites"] });
    },
    onError: (e) => {
      setFormErr(errMsg(e));
    },
  });

  const inviteRevokeM = useMutation({
    mutationFn: (id: string) => revokeAdminInvite(client, id),
    onSuccess: () => {
      toast("Invite revoked.");
      void qc.invalidateQueries({ queryKey: ["admin", "invites"] });
    },
    onError: (e) => {
      setPageErr(errMsg(e));
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
      setFormErr(null);
      toast("Invite link copied to clipboard.");
    } catch {
      setFormErr("Could not copy automatically — select and copy the link below.");
    }
  }

  function openInviteModal(): void {
    setInviteOpen(true);
    setInviteRole("user");
    setInviteAllowsShared(true);
    setLastInviteUrl(null);
    setFormErr(null);
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
        setImportDialogMsg({
          kind: "info",
          text: result.plan.would_succeed
            ? "Preview: import would succeed. Uncheck “Preview only” to create the account."
            : "Preview: import would be refused — see details below.",
        });
        return;
      }
      setLastPlan(null);
      setLastReceipt(result.receipt);
      setImpPass("");
      setImportDialogMsg({
        kind: "info",
        text: "Import complete. The new account is ready. Its first password was set from this form only and is not shown again.",
      });
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
      void qc.invalidateQueries({ queryKey: ["admin", "user-imports"] });
    },
    onError: (e) => {
      setImportDialogMsg({ kind: "error", text: errMsg(e) });
    },
  });

  async function runExportBackup(u: UserAdminView): Promise<void> {
    setExportingId(u.id);
    setPageErr(null);
    let objectUrl: string | null = null;
    try {
      const { blob, filename } = await downloadAdminUserExportZip(client, u.id);
      objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      a.click();
      toast(`Download started for ${u.email}.`);
    } catch (e) {
      setPageErr(errMsg(e));
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

  function openCreateModal(): void {
    setFormErr(null);
    setCreateOpen(true);
  }

  function closeReset(): void {
    setResetFor(null);
    setResetPass("");
    setResetConfirm("");
    setFormErr(null);
  }

  if (listQ.isPending) {
    return (
      <LoadingPlaceholder label="Loading members…" className="max-w-2xl">
        <SkeletonText lines={4} />
      </LoadingPlaceholder>
    );
  }
  if (listQ.isError) {
    return <ErrorState message="Lumogis couldn't load your members." onRetry={() => void listQ.refetch()} />;
  }

  const members = listQ.data ?? [];

  // The confirm dialog's words and action, by kind. The dialog is a confirmation, not the
  // guard: the server still refuses the last admin and self changes.
  const confirmCopy = (() => {
    if (!confirm) return null;
    if (confirm.kind === "role") {
      const email = confirm.user.email;
      return confirm.toAdmin
        ? {
            title: `Make ${email} an Admin?`,
            body: "Admins can manage every household member, including other admins.",
            action: "Make Admin",
            destructive: false,
            run: () => patchM.mutate({ id: confirm.user.id, body: { role: "admin" } }),
          }
        : {
            title: `Change ${email} to Member?`,
            body: "They will lose admin access to household management. They will be signed out on every device, their keys for connected AI apps revoked and any invites they sent cancelled.",
            action: "Make Member",
            destructive: true,
            run: () => patchM.mutate({ id: confirm.user.id, body: { role: "user" } }),
          };
    }
    if (confirm.kind === "disable") {
      return {
        title: `Turn off ${confirm.user.email}?`,
        body: "They will not be able to use Lumogis until you turn their account back on.",
        action: "Turn off",
        destructive: true,
        run: () => patchM.mutate({ id: confirm.user.id, body: { disabled: true } }),
      };
    }
    if (confirm.kind === "delete") {
      return {
        title: `Delete ${confirm.user.email}?`,
        body: "They will be signed out on every device, their keys for connected AI apps revoked and any invites they sent cancelled.",
        action: "Delete",
        destructive: true,
        run: () => delM.mutate(confirm.user.id),
      };
    }
    return {
      title: "Revoke this invite?",
      body: "The link stops working before it is used. You can make a new invite later.",
      action: "Revoke",
      destructive: true,
      run: () => inviteRevokeM.mutate(confirm.invite.id),
    };
  })();

  const memberActions = (u: UserAdminView): RowAction[] => {
    const lastAdmin = isLastActiveAdmin(u);
    const self = isSelf(u);
    const willPromoteToAdmin = u.role !== "admin";
    return [
      {
        label: "Set name",
        onSelect: () => {
          setPageErr(null);
          setNameInput(u.display_name ?? "");
          setNameFor(u);
        },
      },
      {
        // Single source of truth for the role toggle so the label and the PATCH body can
        // never drift apart (`toAdmin` drives both).
        label: `Make ${willPromoteToAdmin ? "Admin" : "Member"}`,
        disabled: lastAdmin,
        onSelect: () => {
          setPageErr(null);
          setConfirm({ kind: "role", user: u, toAdmin: willPromoteToAdmin });
        },
      },
      {
        label: "Reset password",
        onSelect: () => {
          setPageErr(null);
          setFormErr(null);
          setResetPass("");
          setResetConfirm("");
          setResetFor(u);
        },
      },
      {
        label: exportingId === u.id ? "Exporting…" : "Export backup",
        disabled: exportingId === u.id,
        onSelect: () => void runExportBackup(u),
      },
      {
        label: u.disabled ? "Turn on" : "Turn off",
        destructive: !u.disabled,
        // Turning someone back on must stay possible: the old page disabled this
        // control for every turned-off member, so nobody could be re-enabled
        // from the UI (found in LUM-993 plan B Pass 6). Turning on needs no confirm.
        disabled: lastAdmin || self,
        onSelect: () => {
          setPageErr(null);
          if (u.disabled) patchM.mutate({ id: u.id, body: { disabled: false } });
          else setConfirm({ kind: "disable", user: u });
        },
      },
      {
        label: "Delete",
        destructive: true,
        disabled: lastAdmin || self,
        onSelect: () => {
          setPageErr(null);
          setConfirm({ kind: "delete", user: u });
        },
      },
    ];
  };

  /** Why some actions are greyed out. Priority mirrors the backend check order (the
   * last-admin guard is evaluated before the self guard in admin_users.py), so the note
   * stays consistent with the error the API would return if the action went through. */
  function guardNote(u: UserAdminView): string | null {
    if (isLastActiveAdmin(u)) return "The only active admin, so this account can't be changed, turned off or deleted.";
    if (isSelf(u)) return "This is you. You can't turn off or delete your own account — ask another admin.";
    return null;
  }

  const invites = invitesQ.data ?? [];

  const submitCreate = (e: FormEvent) => {
    e.preventDefault();
    setPageErr(null);
    setFormErr(null);
    createM.mutate();
  };

  return (
    <div className="flex flex-col gap-2">
      {pageErr ? (
        <Alert variant="destructive" className="max-w-2xl">
          <AlertDescription>{pageErr}</AlertDescription>
        </Alert>
      ) : null}

      <PageSection
        title="Members"
        description="Everyone who can sign in to this Lumogis. Create an account and share the first password yourself, or send a single-use invite link (valid for 48 hours) so they can sign up on their own."
      >
        <div className="flex flex-col gap-4">
          <p className="m-0 text-sm text-muted-foreground" data-testid="member-summary">
            {memberSummary}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" onClick={openCreateModal}>
              Create user
            </Button>
            <Button type="button" variant="outline" onClick={openInviteModal}>
              Invite member
            </Button>
            <Button type="button" variant="outline" onClick={openImportModal}>
              Import from backup
            </Button>
          </div>
          <div data-testid="admin-users-table-scroll">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Member</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead className="w-12 text-right">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.map((u) => {
                  const note = guardNote(u);
                  return (
                    <TableRow key={u.id}>
                      <TableCell className="min-w-0 whitespace-normal">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium break-all">{u.email}</span>
                          {u.disabled ? <Badge variant="outline">Turned off</Badge> : null}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {u.display_name ? (
                            <span data-testid={`display-name-${u.id}`}>{u.display_name} · </span>
                          ) : null}
                          {u.last_seen_at
                            ? `Last active ${formatLastActive(u.last_seen_at)}`
                            : "No activity yet"}
                        </div>
                        {note ? <div className="text-xs text-muted-foreground">{note}</div> : null}
                      </TableCell>
                      <TableCell>
                        <Badge variant={u.role === "admin" ? "default" : "secondary"}>{roleLabel(u.role)}</Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <RowActions label={`More actions for ${u.email}`} items={memberActions(u)} />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </div>
      </PageSection>

      <PageSection title="Invites" description="Links you have made for people to join. Each works once.">
        {invitesQ.isPending ? (
          <LoadingPlaceholder label="Loading invites…">
            <SkeletonText lines={2} />
          </LoadingPlaceholder>
        ) : invitesQ.isError ? (
          <ErrorState message="Lumogis couldn't load the invites." onRetry={() => void invitesQ.refetch()} />
        ) : invites.length === 0 ? (
          <EmptyState
            title="No invites yet"
            helperText="Invite someone to give them their own account."
            actions={[{ label: "Invite someone", onClick: openInviteModal, primary: true }]}
          />
        ) : (
          <>
            <ul role="list" className="m-0 flex list-none flex-col divide-y divide-border p-0">
              {invites.map((inv) => {
                const status = inviteStatus(inv);
                return (
                  <li key={inv.id} className="flex min-h-12 flex-wrap items-center gap-2 py-1.5">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                        {roleLabel(inv.role)} invite
                        <Badge variant={status === "Active" ? "default" : "secondary"}>{status}</Badge>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {status === "Active" ? "Expires" : "Was due to expire"} {formatDateTime(inv.expires_at)}.{" "}
                        Shared household memory: {inv.role === "admin" || inv.allows_shared ? "yes" : "no"}.
                      </div>
                    </div>
                    {status === "Active" ? (
                      <RowActions
                        label={`Actions for ${roleLabel(inv.role)} invite expiring ${formatDateTime(inv.expires_at)}`}
                        items={[
                          {
                            label: "Revoke",
                            destructive: true,
                            disabled: inviteRevokeM.isPending,
                            onSelect: () => setConfirm({ kind: "revokeInvite", invite: inv }),
                          },
                        ]}
                      />
                    ) : null}
                  </li>
                );
              })}
            </ul>
            <Collapsible className="mt-2">
              <CollapsibleTrigger
                render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
              >
                <ChevronDown
                  aria-hidden="true"
                  className="transition-transform group-aria-expanded/trigger:rotate-180"
                />
                Details
              </CollapsibleTrigger>
              <CollapsibleContent className="pt-2 text-sm">
                <p className="m-0 mb-1 text-muted-foreground">
                  The first characters of each invite link, to tell invites apart.
                </p>
                <ul role="list" className="m-0 list-none p-0">
                  {invites.map((inv) => (
                    <li key={inv.id}>
                      {inviteStatus(inv)}: <code>{inv.token_prefix ?? "—"}</code>
                    </li>
                  ))}
                </ul>
              </CollapsibleContent>
            </Collapsible>
          </>
        )}
      </PageSection>

      {/* Create user */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <form className="grid gap-4" onSubmit={submitCreate}>
            <DialogHeader>
              <DialogTitle>New user</DialogTitle>
              <DialogDescription>
                You choose the first password and share it with them yourself.
              </DialogDescription>
            </DialogHeader>
            <FormError message={formErr} />
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="new-user-email">Email</Label>
              <Input
                id="new-user-email"
                type="email"
                required
                value={nEmail}
                onChange={(e) => setNEmail(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="new-user-password">Password (at least 12 characters)</Label>
              <Input
                id="new-user-password"
                type="password"
                required
                minLength={12}
                value={nPass}
                onChange={(e) => setNPass(e.target.value)}
                autoComplete="new-password"
              />
            </div>
            <RoleSelect id="new-user-role" label="Role" value={nRole} onChange={setNRole} />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={createM.isPending}>
                Create
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Reset password. A form, so a Dialog; the consequence is stated in it. */}
      <Dialog open={resetFor !== null} onOpenChange={(open) => (open ? undefined : closeReset())}>
        <DialogContent>
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              setPageErr(null);
              setFormErr(null);
              resetPwM.mutate();
            }}
          >
            <DialogHeader>
              <DialogTitle>Reset password — {resetFor?.email}</DialogTitle>
              <DialogDescription data-testid="admin-reset-consequence">
                {resetFor ? adminResetConsequence(resetFor.email, resetActiveTokens) : null}
              </DialogDescription>
            </DialogHeader>
            <FormError message={formErr} />
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="reset-password">New password (at least {MIN_PASSWORD_LENGTH} characters)</Label>
              <Input
                id="reset-password"
                type="password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={resetPass}
                onChange={(e) => setResetPass(e.target.value)}
                autoComplete="new-password"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="reset-password-confirm">Confirm password</Label>
              <Input
                id="reset-password-confirm"
                type="password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={resetConfirm}
                onChange={(e) => setResetConfirm(e.target.value)}
                autoComplete="new-password"
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={closeReset}>
                Cancel
              </Button>
              <Button type="submit" disabled={resetPwM.isPending}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Display name (LUM-585) */}
      <Dialog open={nameFor !== null} onOpenChange={(open) => (open ? undefined : setNameFor(null))}>
        <DialogContent>
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!nameFor) return;
              setPageErr(null);
              // Empty clears the name (→ NULL → email local-part attribution).
              patchM.mutate({ id: nameFor.id, body: { display_name: nameInput } });
              setNameFor(null);
            }}
          >
            <DialogHeader>
              <DialogTitle>Display name — {nameFor?.email}</DialogTitle>
              <DialogDescription>
                Shown as &ldquo;Shared by …&rdquo; on this member&apos;s shared items. Leave empty to use their email
                name.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="display-name">Display name</Label>
              <Input
                id="display-name"
                type="text"
                maxLength={64}
                data-testid="display-name-input"
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setNameFor(null)}>
                Cancel
              </Button>
              <Button type="submit">Save</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Invite member */}
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent>
          <div className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Invite member</DialogTitle>
              <DialogDescription>
                Makes a single-use link valid for 48 hours. Copy it and share it securely. The link is shown only once.
              </DialogDescription>
            </DialogHeader>
            <FormError message={formErr} />
            <RoleSelect id="invite-role" label="Role for new member" value={inviteRole} onChange={setInviteRole} />
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <Checkbox
                  id="invite-allows-shared"
                  checked={inviteRole === "admin" ? true : inviteAllowsShared}
                  disabled={inviteRole === "admin"}
                  onCheckedChange={(c) => setInviteAllowsShared(c === true)}
                />
                <Label htmlFor="invite-allows-shared" className="font-normal">
                  Allow access to shared household memory
                </Label>
              </div>
              <p className="m-0 text-xs text-muted-foreground">
                {inviteRole === "admin"
                  ? "Admins always see the shared household memory."
                  : "When off, this member sees only their own personal memory — shared household items stay hidden (system items remain visible)."}
              </p>
            </div>
            {lastInviteUrl ? (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="invite-link">Invite link</Label>
                <Input id="invite-link" readOnly value={lastInviteUrl} />
                {isLoopbackHost(window.location.hostname) ? (
                  <p className="m-0 text-xs text-muted-foreground">
                    This link only works on this computer. Open Lumogis at its network address to invite others.
                  </p>
                ) : null}
              </div>
            ) : null}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setInviteOpen(false)}>
                Close
              </Button>
              {lastInviteUrl ? (
                <Button type="button" variant="secondary" onClick={() => void copyInviteLink()}>
                  Copy link
                </Button>
              ) : null}
              <Button
                type="button"
                disabled={inviteMintM.isPending}
                onClick={() => {
                  setFormErr(null);
                  inviteMintM.mutate();
                }}
              >
                {lastInviteUrl ? "Generate another link" : "Generate invite link"}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      {/* Import from backup */}
      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
          <div className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Import from backup</DialogTitle>
              <DialogDescription>
                Restores a backup file that is already stored on the server. It does not upload a file from your
                computer. The new account&apos;s first password is only sent with this request and is not kept in the
                backup.
              </DialogDescription>
            </DialogHeader>
            {importDialogMsg ? (
              <Alert variant={importDialogMsg.kind === "error" ? "destructive" : "default"} role={importDialogMsg.kind === "error" ? "alert" : "status"}>
                <AlertDescription>{importDialogMsg.text}</AlertDescription>
              </Alert>
            ) : null}
            {archivesQ.isPending && <p className="m-0 text-sm text-muted-foreground">Loading backups…</p>}
            {archivesQ.isError && (
              <Alert variant="destructive">
                <AlertDescription>Could not load the list of backups.</AlertDescription>
              </Alert>
            )}
            {archivesQ.isSuccess && archivesQ.data.length === 0 && (
              <p className="m-0 text-sm">
                No backups found on the server. Use “Export backup” in a member&apos;s “…” menu first.
              </p>
            )}
            {archivesQ.isSuccess && archivesQ.data.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="import-archive">Backup archive</Label>
                <Select
                  value={importArchiveIdx === null ? null : String(importArchiveIdx)}
                  onValueChange={(v) => {
                    setImportArchiveIdx(v == null || v === "" ? null : Number(v));
                    setLastPlan(null);
                    setLastReceipt(null);
                  }}
                >
                  <SelectTrigger id="import-archive" aria-label="Backup archive" className="w-full">
                    <SelectValue>
                      {(v: string | null) => {
                        const a = v == null ? undefined : archivesQ.data[Number(v)];
                        return a ? archiveLabel(a) : "Choose a backup";
                      }}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {archivesQ.data.map((a, i) => (
                      <SelectItem key={`${a.user_id}/${a.archive_filename}`} value={String(i)}>
                        {archiveLabel(a)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="import-email">New account email</Label>
              <Input
                id="import-email"
                type="email"
                required
                value={impEmail}
                onChange={(e) => setImpEmail(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="import-password">New account password (at least {MIN_PASSWORD_LENGTH} characters)</Label>
              <Input
                id="import-password"
                type="password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={impPass}
                onChange={(e) => setImpPass(e.target.value)}
                autoComplete="new-password"
              />
            </div>
            <RoleSelect id="import-role" label="Role" value={impRole} onChange={setImpRole} />
            <div className="flex items-center gap-2">
              <Checkbox
                id="import-dry-run"
                checked={importDryRun}
                onCheckedChange={(c) => {
                  setImportDryRun(c === true);
                  setLastPlan(null);
                  setLastReceipt(null);
                }}
              />
              <Label htmlFor="import-dry-run" className="font-normal">
                Preview only (no account is created)
              </Label>
            </div>
            {lastPlan && (
              <div role="region" aria-label="Import preview" className="flex flex-col gap-1 text-sm">
                <p className="m-0">
                  <strong>{lastPlan.would_succeed ? "The import would work." : "The import would be refused."}</strong>
                </p>
                {(() => {
                  const su = safeExportedUserSummary(lastPlan);
                  if (su.email || su.role) {
                    return (
                      <p className="m-0">
                        Backed up from{" "}
                        {[su.email, su.role ? roleLabel(su.role) : undefined].filter(Boolean).join(" · ")}.
                      </p>
                    );
                  }
                  return null;
                })()}
                {lastPlan.warnings.length > 0 && (
                  <div>
                    <strong>Warnings</strong>
                    <ul className="m-0 pl-5">
                      {lastPlan.warnings.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {lastPlan.missing_sections.length > 0 && (
                  <p className="m-0">Missing from the backup: {lastPlan.missing_sections.join(", ")}.</p>
                )}
              </div>
            )}
            {lastReceipt && (
              <div role="region" aria-label="Import result" className="flex flex-col gap-1 text-sm">
                <p className="m-0">Parts imported: {lastReceipt.sections_imported.length}.</p>
                {(lastReceipt.warnings?.length ?? 0) > 0 && (
                  <div>
                    <strong>Warnings</strong>
                    <ul className="m-0 pl-5">
                      {lastReceipt.warnings!.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setImportOpen(false)}>
                Close
              </Button>
              <Button
                type="button"
                disabled={importM.isPending || !archivesQ.data?.length}
                onClick={() => {
                  setImportDialogMsg(null);
                  importM.mutate();
                }}
              >
                {importDryRun ? "Run preview" : "Run import"}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      {/* Confirmations: change role, turn off, delete, revoke an invite. */}
      <AlertDialog open={confirm !== null} onOpenChange={(open) => (open ? undefined : setConfirm(null))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmCopy?.title}</AlertDialogTitle>
            <AlertDialogDescription>{confirmCopy?.body}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant={confirmCopy?.destructive ? "destructiveSolid" : "default"}
              onClick={() => {
                confirmCopy?.run();
                setConfirm(null);
              }}
            >
              {confirmCopy?.action}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
