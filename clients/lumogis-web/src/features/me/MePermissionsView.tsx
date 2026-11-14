// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-849 — the autonomy page: what may Lumogis do on your behalf, and does it
// ask first.
//
// This replaces a three-column table of `imap` / `smtp` / `ASK` / `DO` that
// stated nothing about what was being decided or what it covered. The defect
// was never the wiring — it was that nobody could answer the question the page
// was asking. So the page now asks it in words, and every raw id and raw mode
// is gone from the visible text.
//
// The page does NOT move (see ADR `autonomy_surface.md`). The tempting fold into
// Email signals was considered and rejected: this is the autonomy surface, and
// its second occupant — a capability's Ask/Do mode — is already enforced in the
// executor today and rendered by nothing (LUM-851).

import { useRef } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "../../api/client";
import { fetchMyPermissions, putMyPermission, type PermRow } from "../../api/permissions";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../_shared/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../_shared/Skeleton";
import { connectorsToRender, groupFor } from "./permissionGroups";

const MODES = ["ASK", "DO"] as const;
type Mode = (typeof MODES)[number];

export function MePermissionsView(): JSX.Element {
  const { client } = useAuth();

  const q = useQuery({
    queryKey: ["me", "permissions"],
    queryFn: () => fetchMyPermissions(client),
  });

  if (q.isPending) {
    return (
      <section>
        <h2>What Lumogis may do</h2>
        <LoadingPlaceholder label="Loading your settings…">
          <SkeletonText lines={4} />
        </LoadingPlaceholder>
      </section>
    );
  }

  if (q.isError) {
    // B4. Reachable only because `api/permissions.ts` throws on non-2xx — if it
    // resolved a failed request instead, this branch would be dead and the page
    // would render an empty permission list, which for THIS page is the worst
    // available lie.
    const err = q.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Your session expired or you are not signed in. Refresh the page or sign in again."
          : err.detail
        : "Something went wrong loading your settings.";
    return (
      <section>
        <h2>What Lumogis may do</h2>
        <ErrorState
          title="We couldn't load what Lumogis may do"
          message={detail}
          onRetry={() => void q.refetch()}
          doctorHint={!(err instanceof ApiError && err.status === 401)}
        />
      </section>
    );
  }

  const { rows, complete } = q.data!;
  const rowByConnector = new Map<string, PermRow>(rows.map((r) => [r.connector, r]));
  // B1 + B2: the union. Neither input alone is correct — see `connectorsToRender`.
  const connectors = connectorsToRender(rows.map((r) => r.connector));

  return (
    <section>
      <h2>What Lumogis may do</h2>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        These decide when Lumogis acts for you without checking first. Everything it
        does either way is recorded in <strong>My activity</strong>.
      </p>

      {!complete && (
        // B3. A successful response that admits it is missing rows. The rows
        // present are still accurate, so they render — this says only that
        // there may be more, which is the honest reading of the advisory.
        <p role="status" style={{ color: "var(--warn, #b45309)", maxWidth: "42rem" }}>
          Some settings couldn&apos;t be loaded just now, so this list may be incomplete.
          What you can see below is accurate.
        </p>
      )}

      {/* No empty state, deliberately. `connectorsToRender` always returns at
          least the known groups, so an "nothing acts on your behalf yet" branch
          would be unreachable — a rendered case that cannot happen, which is the
          same category of untruth as a control that does nothing. If the group
          map ever becomes dynamic, add the branch back WITH a test that reaches
          it. */}
      {connectors.map((connector) => (
        <PermissionGroup
          key={connector}
          connector={connector}
          row={rowByConnector.get(connector)}
        />
      ))}
    </section>
  );
}

/**
 * One question, with its own mutation.
 *
 * **The mutation lives here and not in the parent, and that is load-bearing.**
 * A single `useMutation` shared across groups tracks only the MOST RECENTLY
 * dispatched call: `MutationObserver.mutate()` detaches the observer from the
 * in-flight mutation before attaching the new one, so `isPending`, `isError`
 * and `variables` all switch to the newer one and the older mutation's outcome
 * never reaches the component. Scoping by `variables?.connector === connector`
 * reads like it fixes that and does not.
 *
 * Concretely, with a shared mutation: click "Just send" on this group, then
 * click the other group before the first PUT lands — this group's radios
 * re-enable while its PUT is still in flight (so a second click writes the same
 * permission change twice), and if that first PUT then fails, the "didn't save"
 * message can never appear, because `variables.connector` is stuck on the other
 * group. Both are the exact failures B5 exists to prevent, and the fix that
 * created B5 is what introduced them.
 *
 * One component per group means one mutation per group: `pending` and `failed`
 * are this connector's, by construction rather than by comparison.
 */
function PermissionGroup({
  connector,
  row,
}: {
  connector: string;
  row: PermRow | undefined;
}): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const group = groupFor(connector);

  const putM = useMutation({
    mutationFn: (mode: Mode) => putMyPermission(client, connector, mode),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["me", "permissions"] }),
  });

  const failed = putM.isError ? putM.error : null;

  // `disabled={putM.isPending}` only takes effect on the next render, so a fast
  // double-click can reach onChange twice first: two writes of one permission
  // change. This ref closes that gap synchronously; it clears when the PUT settles.
  const inFlight = useRef(false);
  const save = (mode: Mode) => {
    if (inFlight.current) return;
    inFlight.current = true;
    putM.mutate(mode, {
      onSettled: () => {
        inFlight.current = false;
      },
    });
  };

  return (
    <fieldset
      data-testid={`perm-group-${connector}`}
      style={{
        border: "1px solid rgba(128,128,128,0.25)",
        borderRadius: 8,
        padding: "1rem",
        margin: "1rem 0",
        maxWidth: "42rem",
      }}
    >
      <legend style={{ fontWeight: 700, padding: "0 0.35rem" }}>{group.title}</legend>
      <p style={{ margin: "0 0 0.35rem" }}>{group.detail}</p>
      <p style={{ margin: "0 0 0.75rem", opacity: 0.85 }}>{group.consequence}</p>

      {row === undefined ? (
        // B1. A known question whose row the server could not resolve. It
        // renders its question and says so — it does NOT render a control,
        // because a control implies a state and we have not been told one.
        // Critically it must never read "Ask me first": `is_default: true` is a
        // statement the server makes, and an absent row is the absence of any
        // statement.
        <p role="status" style={{ margin: 0, color: "var(--warn, #b45309)" }}>
          We couldn&apos;t load this setting right now.
        </p>
      ) : (
        <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap" }}>
          {MODES.map((mode) => (
            <label key={mode} style={{ display: "flex", gap: "0.4rem" }}>
              <input
                type="radio"
                name={`perm-${connector}`}
                value={mode}
                checked={row.mode === mode}
                // B5. There is deliberately no optimistic update — the control
                // reflects server state only — so without this the radio would
                // not move until the PUT AND its refetch both landed. A person
                // clicks, sees nothing change, and clicks again: two writes of
                // a permission change for one intent.
                disabled={putM.isPending}
                onChange={() => save(mode)}
              />
              {mode === "ASK" ? group.askLabel : group.doLabel}
              {mode === "ASK" && row.is_default && (
                <span style={{ opacity: 0.6 }}>(default)</span>
              )}
            </label>
          ))}
        </div>
      )}

      <p style={{ margin: "0.75rem 0 0", fontStyle: "italic", opacity: 0.8 }}>
        {group.scopeNote}
      </p>

      {failed && (
        <p role="alert" style={{ margin: "0.5rem 0 0", color: "var(--error, #b91c1c)" }}>
          That didn&apos;t save
          {failed instanceof ApiError ? `: ${failed.detail}` : "."} The setting is unchanged.
        </p>
      )}
    </fieldset>
  );
}
