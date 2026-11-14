// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import type { ModelsResponse } from "../../api/models";
import { testConnector, type ConnectorTestResult } from "../../api/connectorCredentials";
import { getCredentialFormComponent } from "../credentials/forms";
import { PrivacyBlockedHint } from "../_shared/PrivacyBlockedHint";
import { catalogHasPrivacyBlock } from "../_shared/privacyBlockCopy";

/** One declared field of a connector's payload — name, label, and secrecy. */
interface RegField {
  name: string;
  label: string;
  is_secret: boolean;
}
interface RegItem {
  id: string;
  description: string;
  /** Empty means "this connector's shape is not described", NOT "no fields". */
  fields?: RegField[];
}
interface CCRow {
  connector: string;
  updated_at: string;
  enabled?: boolean;
  /**
   * THREE states. `undefined`/`null` is UNKNOWN — the row predates migration
   * 079 — and is NOT "nothing set". See `connectorCredentials.ts` for why
   * `?? []` is forbidden on this field.
   */
  configured_fields?: string[] | null;
  secret_fields?: string[];
}
/** The caller's own credential surface — the only one `/test` exists on. */
export const ME_CONNECTORS_BASE = "/api/v1/me/connector-credentials";

/** Where mailbox connectors are actually tested: pre-save, with folder detection. */
const MAILBOX_SURFACE = "/me/email-signals";

/**
 * Connectors the backend answers with `tested_elsewhere` — they HAVE a
 * connection test, just not on this route. Both are multi-instance and a
 * connector-level path has nowhere to put a mailbox id: `imap` is tested by
 * `POST /me/email-sources/test`, `smtp` by the send opt-in's pre-save connect
 * test. Both live on the Email signals page.
 *
 * MUST equal `_TESTED_ELSEWHERE` in `services/connector_probes.py`. One idea in
 * two languages is a duplication, and drift here puts a button on a card that
 * can only ever answer "test this somewhere else" — so a backend test reads
 * this list out of this file and pins the two equal, rather than a comment
 * asking politely.
 */
const TESTED_ELSEWHERE = new Set(["imap", "smtp"]);

/**
 * Connectors that render NO card here (LUM-848).
 *
 * `imap` and `smtp` are credentialed per mailbox from Email signals, and this
 * surface PUTs without an `instance` — so every save was a `422
 * instance_required`. A card that cannot save is the "surface that runs and
 * shows nothing" defect in its mildest form.
 *
 * Hiding alone would recreate the same defect pointing the other way: a user
 * looking for "where do I configure my email?" would find NOTHING, with no
 * indication the answer exists elsewhere. So a signpost renders in their place
 * — the difference between removing a dead end and removing the map.
 */
const HIDDEN_FROM_CARDS = new Set(["imap", "smtp"]);

export function MeConnectorsView({ llmOnly = false, basePath = ME_CONNECTORS_BASE }: { llmOnly?: boolean; basePath?: string }): JSX.Element {
  const { client } = useAuth();
  const [msg, setMsg] = useState<string | null>(null);

  // Test-connection is a USER-ONLY route. `AdminConnectorCredentialsView`
  // renders this same component across three tiers with three different
  // basePaths, and `probe(connector, user_id)` has no tier axis — so a Test
  // button there would silently test the ADMIN'S OWN credential while
  // presenting as a household or system test. Two of the three tabs would show
  // a button that answers the wrong question. The admin mirror is deferred
  // until the probe signature carries a tier.
  const canTest = basePath === ME_CONNECTORS_BASE;

  // The enable/disable toggle is per-USER, and there is deliberately no admin
  // PATCH: `enabled` is a column on `user_connector_credentials` only. The
  // household and system tables have no such column, and on the admin
  // per-user tab the model carries `enabled` but nothing can write it — so the
  // toggle renders read-only-by-absence rather than as a control that 404s.
  const canToggle = basePath === ME_CONNECTORS_BASE;

  // Field presence needs the `configured_keys` column, which migration 079 put
  // on `user_connector_credentials` only. The per-user admin tab reads that
  // same table, so it gets field rows; household and system do not have the
  // column and never will under this chunk.
  const supportsFieldPresence = !basePath.includes("/connector-credentials/household")
    && !basePath.includes("/connector-credentials/system");

  // G5 (inline Ask/Do) is NOT shipped here, and the reason is a conflict
  // between two decisions this plan made separately.
  //
  // `PUT /api/v1/me/permissions/{connector}` validates against the ACTION
  // registry (`routes/connector_permissions.py:186-199`), whose only members
  // are `imap` and `smtp` — because a permission mode gates ACTIONS, and no
  // other connector registers any. Those two are exactly what owner decision 4
  // removes from this card. So an inline Ask/Do control would have rendered on
  // paperless, caldav, ntfy and the six llm_* connectors and returned
  // `404 unknown_connector` on every change, while the list GET returned no row
  // for them so the control always read "Ask me first".
  //
  // A control that 404s on every click is the "surface that runs and shows
  // nothing" defect this arc is named after, so it is not shipped.
  //
  // AND THE SETTING DOES NOT BELONG HERE ANYWAY. Ask/Do only means something
  // for a connector that acts ON YOUR BEHALF, and none of the nine cards on
  // this page can: paperless and caldav read, ntfy pushes to you, the llm_*
  // ones answer questions. Nothing leaves as you. Email is the only family
  // that acts — and it is exactly what this page hides (LUM-848).
  //
  // The resolution (LUM-849) is to KEEP `/me/permissions`, fix its copy, and
  // cross-link to it — NOT to fold it into Email signals. That page is the
  // AUTONOMY page ("what may Lumogis do on my behalf"), and its second occupant
  // is a `capability.{id}` mode, which is ENFORCED today and rendered nowhere:
  // `execute_capability_http` -> `may_execute` -> `check_permission` ->
  // `get_connector_mode("capability.{id}")` (`services/execution.py:280`).
  // Folding Ask/Do into Email signals would encode "autonomy = email" and leave
  // that second occupant with nowhere coherent to go.
  //
  // NOTE: an earlier version of this comment said the second occupant "already
  // ships elsewhere" on `/me/tools-capabilities`. It does not. That view calls
  // `GET /api/v1/me/tools` (a read-only tool catalog) and never touches
  // `GET /api/v1/me/permissions/capabilities`, which has no web consumer at all.
  // See `.cursor/adrs/autonomy_surface.md`.

  const regQ = useQuery({
    queryKey: ["cc", "registry"],
    queryFn: () => client.getJson<{ items: RegItem[] }>("/api/v1/me/connector-credentials/registry"),
  });

  const listQ = useQuery({
    queryKey: ["cc", "list", basePath],
    queryFn: () => client.getJson<{ items: CCRow[] }>(basePath),
  });

  // TWO list requests, never one per connector: registry and credentials.
  // (The third — permissions — is not made, because Ask/Do is not on this card;
  // see the note above.) A per-card query for anything would be one request per
  // connector, which is the LUM-744 pattern this codebase has already paid for.

  const listItems = useMemo(() => listQ.data?.items ?? [], [listQ.data?.items]);
  const rowByConnector = useMemo(
    () => new Map(listItems.map((i) => [i.connector, i])),
    [listItems],
  );
  const existing = useMemo(() => new Set(listItems.map((i) => i.connector)), [listItems]);
  const fromRegistry = useMemo(
    () => (regQ.data?.items ?? []).filter((i) => (llmOnly ? i.id.startsWith("llm_") : true)),
    [regQ.data, llmOnly],
  );

  const items: RegItem[] = useMemo(() => {
    const base = regQ.isSuccess
      ? fromRegistry
      : regQ.isError
        ? listItems
            .filter((i) => (llmOnly ? i.connector.startsWith("llm_") : true))
            .map((i) => ({ id: i.connector, description: "" }))
        : [];
    return base.filter((i) => !HIDDEN_FROM_CARDS.has(i.id));
  }, [regQ.isSuccess, regQ.isError, fromRegistry, listItems, llmOnly]);

  if (listQ.isPending) return <p>Loading…</p>;
  if (regQ.isPending && !regQ.isError) return <p>Loading…</p>;

  return (
    <section>
      <h2>{llmOnly ? "LLM providers" : "Connectors"}</h2>
      {regQ.isError && (
        <p role="status">Connector schema hints unavailable; using JSON fallback for listed connectors.</p>
      )}
      {msg && <p role="status">{msg}</p>}
      {/*
        The signpost replacing the imap/smtp cards (LUM-848). Gated to the
        caller's OWN surface, and not just because email is irrelevant on the
        LLM-providers page: `/me/email-signals` is a ME page, so rendering this
        on an admin household or system tab would point an admin at their own
        mailboxes while presenting as the tier they are administering — the
        same wrong-question bug `canTest` and `canSetMode` exist to prevent,
        in miniature.
      */}
      {basePath === ME_CONNECTORS_BASE && !llmOnly && (
        <p>
          Email mailboxes and sending are set up on the{" "}
          <Link to={MAILBOX_SURFACE}>Email signals</Link> page.
        </p>
      )}
      {items.map((item) => (
        <ConnectorCard
          key={item.id}
          connector={item.id}
          description={item.description}
          fields={item.fields}
          row={rowByConnector.get(item.id)}
          isEdit={existing.has(item.id)}
          canTest={canTest}
          canToggle={canToggle}
          supportsFieldPresence={supportsFieldPresence}
          basePath={basePath}
          onStatusMessage={setMsg}
        />
      ))}
    </section>
  );
}

/** The four-state chip's state, derived from the row and the last test. */
type ChipState = "not-configured" | "configured" | "verified" | "failed";

/**
 * Which of the four chip states to show.
 *
 * Deliberately does NOT read `configured_fields`: "configured" here means the
 * row exists, and a row cannot exist without a payload. The three-state
 * `configured_fields` read — where the `?? []` ban actually applies — lives in
 * `FieldPresence` below.
 */
function chipState(row: CCRow | undefined, result: ConnectorTestResult | null): ChipState {
  if (row === undefined) return "not-configured";
  if (result?.ok === true) return "verified";
  if (result?.ok === false) return "failed";
  return "configured";
}

const CHIP_LABEL: Record<ChipState, string> = {
  "not-configured": "Not configured",
  configured: "Configured (untested)",
  verified: "Verified",
  failed: "Failed",
};

/**
 * One connector card, with its own save/enable/delete mutations.
 *
 * **The mutations live here and not in the parent, and that is load-bearing.**
 * A single `useMutation` shared across cards tracks only the MOST RECENTLY
 * dispatched call: `MutationObserver.mutate()` detaches the observer from the
 * in-flight mutation before attaching the new one, so `isPending`, `isError`
 * and `variables` all switch to the newer one and the older mutation's outcome
 * never reaches the component. Scoping by `variables?.connector === connector`
 * reads like it fixes that and does not.
 *
 * Concretely, with a shared mutation: save paperless, then toggle caldav before
 * the first PUT lands — paperless's form re-enables while its PUT is still in
 * flight (so a second save can write stale data), and if that first PUT then
 * fails, the error can never appear, because `variables.connector` is stuck on
 * caldav. Whole-payload replacement means the older PUT completing after a newer
 * one destroys the credential the user just saved.
 */
function ConnectorCard({
  connector,
  description,
  fields,
  row,
  isEdit,
  canTest,
  canToggle,
  supportsFieldPresence,
  basePath,
  onStatusMessage,
}: {
  connector: string;
  description: string;
  fields?: RegField[];
  row?: CCRow;
  isEdit: boolean;
  canTest: boolean;
  canToggle: boolean;
  supportsFieldPresence: boolean;
  basePath: string;
  onStatusMessage: (msg: string | null) => void;
}): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const Form = getCredentialFormComponent(connector);
  const [result, setResult] = useState<ConnectorTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  // LUM-881: true only while a re-read of the caller's model catalog, after an
  // `llm_*` save, shows a model blocked by privacy mode. This card is the one
  // place every `llm_*` save passes (/me/connectors, /me/llm-providers and the
  // three admin tiers), so the hint lives here and not in `LlmApiKeyForm`. It
  // re-reads the catalog rather than assuming the key counts: a saved key may
  // not make any model usable (auth-off Web-saved key), and then privacy mode
  // is not what is in the way.
  const [keySaveBlocked, setKeySaveBlocked] = useState(false);

  const enabled = row?.enabled !== false;

  const putM = useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      await client.putJson(`${basePath}/${encodeURIComponent(connector)}`, { payload });
    },
    onMutate: () => {
      setSaveError(null);
      setKeySaveBlocked(false);
      onStatusMessage(null);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cc", "list"] });
      onStatusMessage("Saved.");
      if (connector.startsWith("llm_")) {
        // The catalog is computed for the caller, whose credential snapshot
        // already unions the per-user, household and system tiers, so one
        // check is right on every tier. A failed read hides the hint.
        client.getJson<ModelsResponse>("/api/v1/models").then(
          (r) => setKeySaveBlocked(catalogHasPrivacyBlock(r.models)),
          () => setKeySaveBlocked(false),
        );
      }
    },
    onError: (e) => {
      setSaveError(e instanceof ApiError ? e.detail : "Save failed");
    },
  });

  const delM = useMutation({
    mutationFn: async () => {
      await client.delete(`${basePath}/${encodeURIComponent(connector)}`);
    },
    onMutate: () => onStatusMessage(null),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cc", "list"] });
      // Not in onMutate: a failed delete leaves the key, so the hint stays.
      setKeySaveBlocked(false);
      onStatusMessage("Deleted.");
    },
    onError: (e) => {
      setSaveError(e instanceof ApiError ? e.detail : "Delete failed");
    },
  });

  const enableM = useMutation({
    mutationFn: async (nextEnabled: boolean) => {
      await client.patchJson(`${basePath}/${encodeURIComponent(connector)}`, { enabled: nextEnabled });
    },
    onMutate: () => {
      setSaveError(null);
      onStatusMessage(null);
    },
    onSuccess: (_d, nextEnabled) => {
      void qc.invalidateQueries({ queryKey: ["cc", "list"] });
      onStatusMessage(nextEnabled ? "Turned on." : "Turned off. The credential is kept.");
    },
    onError: (e) => {
      setSaveError(e instanceof ApiError ? e.detail : "Could not change that");
    },
  });

  const testM = useMutation({
    mutationFn: () => testConnector(client, connector),
    onMutate: () => {
      setTestError(null);
      setResult(null);
    },
    onSuccess: (r) => setResult(r),
    onError: (e) => {
      // The probe itself never rejects — a failed test is a 200. Only a
      // refusal reaches here, and 429 is the one a user can actually act on.
      if (e instanceof ApiError && e.status === 429) {
        setTestError("Too many attempts. Wait a minute.");
        return;
      }
      setTestError(e instanceof ApiError ? e.detail : "Test failed");
    },
  });

  return (
    <article style={{ border: "1px solid #444", borderRadius: 6, padding: "0.75rem", marginBottom: "0.75rem" }}>
      <h3 style={{ marginTop: 0 }}>
        {connector}
        <span style={{ fontSize: "0.75rem", marginLeft: "0.5rem", opacity: 0.7 }}>
          {CHIP_LABEL[chipState(row, result)]}
        </span>
        {isEdit && !enabled && (
          <span style={{ fontSize: "0.75rem", marginLeft: "0.5rem" }}>· Off</span>
        )}
      </h3>
      <p style={{ fontSize: "0.85rem" }}>{description}</p>

      <FieldPresence
        fields={fields}
        row={row}
        isEdit={isEdit}
        supportsFieldPresence={supportsFieldPresence}
      />

      {canToggle && isEdit && (
        <p>
          <label>
            <input
              type="checkbox"
              checked={enabled}
              disabled={enableM.isPending || putM.isPending}
              onChange={(e) => enableM.mutate(e.currentTarget.checked)}
            />{" "}
            On
          </label>{" "}
          <span style={{ fontSize: "0.8rem", opacity: 0.8 }}>
            Turning this off keeps the credential — you will not have to enter it again.
          </span>
        </p>
      )}

      {/*
        Editable while disabled, on purpose: a user needs to be able to fix a
        credential BEFORE switching it back on, and saving does not re-enable
        (`put_payload` leaves `enabled` alone).
      */}
      <fieldset disabled={putM.isPending} style={{ border: "none", margin: 0, padding: 0 }}>
        <Form
          value={{}}
          isEdit={isEdit}
          onChange={(payload) => putM.mutate(payload)}
          hint="Payload is sent encrypted to the server; use typed fields where available."
        />
      </fieldset>
      {keySaveBlocked ? (
        <PrivacyBlockedHint surface="key-save" adminTier={basePath !== ME_CONNECTORS_BASE} />
      ) : null}
      {saveError && (
        <p role="alert" style={{ fontSize: "0.85rem", color: "salmon" }}>
          {saveError}
        </p>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.5rem" }}>
        {canTest &&
          (TESTED_ELSEWHERE.has(connector) ? (
            // A dead Test button is worse than no button. A round trip whose
            // only possible answer is "test this somewhere else" is a button
            // that wastes a click to deliver a link — so render the link.
            <Link to={MAILBOX_SURFACE}>Test this on the Email signals page</Link>
          ) : (
            <button
              type="button"
              onClick={() => testM.mutate()}
              // Disabled connectors answer `code: "disabled"` honestly rather
              // than lying in either direction, but there is nothing to learn
              // from asking — so the button says so before the round trip.
              disabled={testM.isPending || (isEdit && !enabled)}
            >
              {testM.isPending ? "Testing…" : "Test connection"}
            </button>
          ))}
        {isEdit && (
          <button
            type="button"
            disabled={delM.isPending}
            onClick={() => {
              if (window.confirm(`Delete ${connector}?`)) delM.mutate();
            }}
          >
            Delete
          </button>
        )}
      </div>
      {testError && <p role="alert" style={{ fontSize: "0.85rem" }}>{testError}</p>}
      {result && <TestChip result={result} />}
    </article>
  );
}

/**
 * Which fields are set — computed without decrypting anything.
 *
 * Three cases the card must keep distinct, because collapsing any pair makes it
 * say something untrue:
 *
 *  1. **No declared schema** (`fields` empty) — "this connector's shape is not
 *     described". NOT "no fields are set": those are opposite claims.
 *  2. **`configured_fields` unknown** (null/undefined on a configured row) —
 *     the row predates migration 079. "Configured", with details after the next
 *     save. NOT "nothing set" about a working credential.
 *  3. **Known** — tick per declared field.
 *
 * Secret fields get CONSEQUENCE COPY rather than a bare ✓. Saving still
 * requires the secret (there is no backend merge, so omitting a field deletes
 * it), and a card that says "set" beside a value it then refuses to reuse is
 * worse than one that says nothing — it announces the value is stored and then
 * makes the user find it again.
 */
function FieldPresence({
  fields,
  row,
  isEdit,
  supportsFieldPresence,
}: {
  fields?: RegField[];
  row?: CCRow;
  isEdit: boolean;
  supportsFieldPresence: boolean;
}): JSX.Element | null {
  if (!isEdit || row === undefined) return null;
  // The household and system tiers have NO `configured_keys` column —
  // migration 079 touches `user_connector_credentials` only — and their wire
  // models (`HouseholdConnectorCredentialPublic`,
  // `InstanceSystemConnectorCredentialPublic`) will never carry
  // `configured_fields`. Falling through to the unknown branch below would
  // render "Field details appear after the next save" on those tabs FOREVER,
  // because the next save cannot populate a column that does not exist. On a
  // card whose whole purpose is never to say an untrue sentence, that is the
  // untrue sentence, so those surfaces render nothing here instead.
  if (!supportsFieldPresence) return null;
  if (fields === undefined || fields.length === 0) {
    return <p style={{ fontSize: "0.85rem" }}>This connector&rsquo;s fields are not described.</p>;
  }

  // Explicit destructure — `?? []` is FORBIDDEN here. It is this codebase's
  // dominant idiom for an optional array, and using it would turn "unknown"
  // into "nothing set" and render a working credential as unconfigured.
  const configured = row.configured_fields;
  if (configured === undefined || configured === null) {
    return (
      <p style={{ fontSize: "0.85rem" }}>Configured. Field details appear after the next save.</p>
    );
  }

  // The third arm of the migration-014 triad the server defends at length:
  // `[]` is IMPOSSIBLE for a real credential (`put_payload` rejects an empty
  // payload), so it means a data error — not "known, and nothing is set".
  // Rendering it as every-field-absent would quietly report a broken row as an
  // ordinary unconfigured one.
  if (configured.length === 0) {
    return (
      <p style={{ fontSize: "0.85rem" }} role="status">
        Field details for this credential look wrong. Saving it again will fix them.
      </p>
    );
  }

  const set = new Set(configured);
  const secrets = new Set(row.secret_fields ?? []);
  return (
    <ul style={{ fontSize: "0.85rem", listStyle: "none", paddingLeft: 0 }}>
      {fields.map((f) => {
        const isSet = set.has(f.name);
        if (secrets.has(f.name)) {
          return (
            <li key={f.name}>
              <strong>{f.label}</strong>
              {isSet
                ? " — saved. Changing anything on this card means entering it again."
                : " — not set."}
            </li>
          );
        }
        return (
          <li key={f.name}>
            {isSet ? "✓" : "—"} {f.label}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The tri-state chip.
 *
 * `ok === null` means NOT ATTEMPTED and must render neither ✓ nor ✗. A ✗ on a
 * connector with no probe is a red mark on something that is working fine —
 * this arc's recurring "a surface that says the wrong thing" defect.
 */
function TestChip({ result }: { result: ConnectorTestResult }): JSX.Element {
  const mark = result.ok === true ? "✓" : result.ok === false ? "✗" : "";
  return (
    <p role="status" style={{ fontSize: "0.85rem", marginTop: "0.5rem" }}>
      {mark && <span aria-hidden="true">{mark} </span>}
      {result.detail}
    </p>
  );
}
