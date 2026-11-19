// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-886 — two source-scanning guards that make the "per-member browser state registers
// with the sign-out boundary" contract mechanical (the plan's tests 56 and 57):
//
//  * no wholesale clear: `sessionStorage.clear()`, `localStorage.clear()` and idb-keyval
//    `clear()` would destroy the login flash, device preferences and the capture outbox
//    (the only copy of an unsynced capture);
//  * storage inventory: every `src` file that touches browser storage is classified below,
//    so a NEW file doing so fails CI until someone decides whether its keys are per-member
//    (add a step to `auth/clientStateBoundary.ts`), the only copy (owner-fence it) or
//    device/flow state (keep it).

import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "src");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.(ts|tsx)$/.test(name) && !name.includes("/generated/") && !full.includes(`${path.sep}generated${path.sep}`)) {
      out.push(full);
    }
  }
  return out;
}

/** Drop comments so prose that names a forbidden call does not trip the guard. */
function stripComments(text: string): string {
  // Line comments first: a line comment such as `// legacy /settings/*.` must not open a block.
  return text.replace(/(^|[^:"'`])\/\/.*$/gm, "$1").replace(/\/\*[\s\S]*?\*\//g, "");
}

const files = walk(SRC).map((f) => ({
  rel: path.relative(SRC, f).split(path.sep).join("/"),
  code: stripComments(readFileSync(f, "utf8")),
}));

/** Every src file that touches browser storage, with its class from the plan's storage contract. */
const INVENTORY: Record<string, string> = {
  "api/invites.ts": "flow: invite onboarding hint — removed by the full sign-out wipe (clearInviteOnboardingHint)",
  "auth/AuthProvider.tsx": "flow: login flash read — kept (never cleared wholesale)",
  "auth/clientStateBoundary.ts": "boundary: the only place that wipes per-member state",
  "components/GraphOfferLine.tsx": "device/flow: dismissed knowledge-graph offer line (a household setting, not per member) — kept",
  "components/SecureAddressNotice.tsx": "device/flow: dismissed secure-address notice (the household address, same for every member) — kept",
  "design/devTools.ts": "device/flow: show-developer-tools display choice (LUM-993) — kept",
  "design/sidebarCollapse.ts": "device/flow: kept",
  "design/theme.ts": "device/flow: kept",
  "features/admin/AdminSystemStatusView.tsx": "device/flow: admin update-dismiss — kept",
  "features/auth/ResetPasswordPage.tsx": "device/flow: login flash writer — kept",
  "features/chat/threadStore.ts": "per-member: wiped by clientStateBoundary (clearPersistedThreads)",
  "features/email/activation.ts": "per-member hint: wiped by clientStateBoundary (clearCachedEmailActivated)",
  "features/invite/InviteRedemptionPage.tsx": "flow: invite onboarding hint writer — removed by the full sign-out wipe",
  "features/me/MeProfileView.tsx": "device/flow: login flash writer — kept",
  "features/onboarding/OnboardingGate.tsx": "flow: invite onboarding hint reader",
  "features/onboarding/useOnboardingStatus.ts": "flow: invite onboarding hint remover",
  "pwa/captureOutbox.ts": "only copy: owner-fenced by owner_user_id, never wiped",
  "pwa/drafts.ts": "per-member: wiped by clientStateBoundary (clearAllDrafts, by prefix)",
  "pwa/queryPersistence.ts": "per-member (empty allowlist today): wiped by clientStateBoundary (clearPersistedQueryCache)",
};

describe("no wholesale storage clear (LUM-886 test 56)", () => {
  it("no src file calls sessionStorage.clear() or localStorage.clear(), optional chaining included", () => {
    const offenders = files.filter((f) => /(?:session|local)Storage\s*\??\.\s*clear\s*\(/.test(f.code)).map((f) => f.rel);
    expect(offenders).toEqual([]);
  });

  it("no file that imports idb-keyval calls or imports its clear()", () => {
    const offenders = files
      .filter((f) => /from\s+["']idb-keyval["']/.test(f.code))
      .filter((f) => /\bclear\s*\(/.test(f.code) || /import\s*\{[^}]*\bclear\b[^}]*\}\s*from\s+["']idb-keyval["']/.test(f.code))
      .map((f) => f.rel);
    expect(offenders).toEqual([]);
  });
});

describe("storage inventory (LUM-886 test 57)", () => {
  it("every src file that touches browser storage is classified", () => {
    const touching = files
      .filter((f) => /\b(?:localStorage|sessionStorage)\b|from\s+["']idb-keyval["']/.test(f.code))
      .map((f) => f.rel)
      .sort();
    expect(touching).toEqual(Object.keys(INVENTORY).sort());
  });
});
