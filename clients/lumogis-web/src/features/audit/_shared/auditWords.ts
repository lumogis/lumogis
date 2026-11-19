// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Plain sentences for audit rows (LUM-993 plan B Pass 6). The server records
// stable codes (`__session__.minted`, `privacy.external_call.denied`); a person
// reads "Signed in". The raw code stays in the row's Details. Names mirror
// orchestrator/services/audit_taxonomy.py; anything unmapped falls back to a
// tidied version of the code rather than hiding the row.

import type { AuditEntry } from "../../../api/audit";

const ACTION_SENTENCES: Record<string, string> = {
  "__session__.minted": "Signed in",
  "__session__.revoked": "Signed out",
  "__session__.admin_revoked": "A sign-in was ended by an admin",
  "__session__.cascade_revoked": "Sign-ins ended after a password or account change",
  "__session__.reuse_detected": "A sign-in was ended because it looked stolen",
  "__user_invite__.minted": "An invitation was created",
  "__user_invite__.redeemed": "An invitation was accepted",
  "__user_invite__.revoked": "An invitation was cancelled",
  "__mcp_token__.minted": "An app key was created",
  "__mcp_token__.revoked": "An app key was removed",
  "__mcp_token__.admin_revoked": "An app key was removed by an admin",
  "__mcp_token__.cascade_revoked": "An app key was removed automatically",
  "__connector_credential__.put": "Connection details were saved",
  "__connector_credential__.deleted": "Connection details were removed",
  "__connector_credential__.rotated": "Connection details were replaced",
  "__user_export__.started": "A data export started",
  "__user_export__.completed": "A data export finished",
  "__user_export__.failed": "A data export failed",
  "__user_export__.prune_failed": "Old data exports could not be cleaned up",
  "__user_import__.refused": "A data import was refused",
  "__user_import__.dry_run_requested": "A data import was checked first",
  "__user_import__.dry_run_validation_passed": "A data import check passed",
  "__user_import__.dry_run_validation_failed": "A data import check failed",
  "__user_import__.started": "A data import started",
  "__user_import__.completed": "A data import finished",
  "__user_import__.failed": "A data import failed",
  "__password_reset__.requested": "A password reset was requested",
  "__password_reset__.redeemed": "A password was changed with a reset link",
  "__system_mail__.dead_lettered": "A system email could not be delivered",
  privacy_mode_block: "An online AI call was blocked by the privacy setting",
  capture_index: "A capture was added to your memory",
  "tool.execute.capability": "A tool was used",
  email_reply: "Replied to an email",
  email_compose: "Wrote an email",
  email_forward: "Forwarded an email",
  email_unsubscribe: "Unsubscribed from a mailing list",
  email_save_draft_reverse: "A draft was undone",
  send_email: "Sent an email",
  undo_write: "Undid a change",
  admin_unshare: "An admin stopped sharing an item",
};

const EVENT_SENTENCES: Record<string, string> = {
  "privacy.external_call.denied": "An online AI call was blocked by the privacy setting",
  "data.capture.indexed": "A capture was added to your memory",
  "auth.permissions.changed": "What Lumogis may do was changed",
  "action.routine.executed": "A routine ran",
  "action.executed": "An action ran",
};

function tidy(code: string): string {
  const words = code
    .split(".")
    .map((part) => part.replace(/^_+|_+$/g, "").replace(/_/g, " ").trim())
    .filter(Boolean)
    .join(" ");
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Activity recorded";
}

/** One plain sentence for "what happened"; the raw code is shown in Details. */
export function auditSentence(row: Pick<AuditEntry, "action_name" | "event_type">): string {
  const name = row.action_name ?? "";
  if (ACTION_SENTENCES[name]) return ACTION_SENTENCES[name];
  if (name.startsWith("__permissions_change__")) return EVENT_SENTENCES["auth.permissions.changed"]!;
  if (name.startsWith("routine:")) return EVENT_SENTENCES["action.routine.executed"]!;
  // A specific action name with no sentence reads better than the catch-all bucket.
  if (name && row.event_type === "action.executed") return tidy(name);
  if (row.event_type && EVENT_SENTENCES[row.event_type]) return EVENT_SENTENCES[row.event_type]!;
  return tidy(name || row.event_type || "");
}
