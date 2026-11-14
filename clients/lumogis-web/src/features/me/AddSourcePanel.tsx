// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-837 chunk B — adding a source, and the two consent gates on that route.
//
// `POST /sources` shipped with a two-step `confirm=false` preview / `confirm=true`
// save flow that had never had a caller, and with two consent arms — LUM-729's
// starvation gate and LUM-835's duplicate-filing gate — that were reachable only
// by someone reading a `422` from `curl`. This is their first client.
//
// Split out of `AdminSourcesView` rather than inlined: the list is a table and
// this is a state machine with three async reads, and one component doing both
// would bury the part that needed four rounds of review to specify.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useReducer, useState } from "react";

import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { listMyCredentials } from "../../api/connectorCredentials";
import { createSource, previewSource, type SourcePreview } from "../../api/sources";
import { OVERLAP_FALLBACK, paperlessOverlapWarning } from "../email/paperlessOverlapWarning";
import { starvationWarning } from "../signals/externalConsumerWarning";
import {
  consentFlags,
  consentReducer,
  initialConsentState,
  type ConsentState,
} from "./sourceConsent";

// The shipped key and the shipped call — `EmailSignalsView` already reads
// credential presence exactly this way. A third key family for one resource is
// how the badge ends up watching a key nothing invalidates.
const CREDENTIALS_KEY = ["me", "connector-credentials"] as const;

// `SourceRequest.source_type` is `Literal["paperless", "imap"] | None`, but
// `POST /sources` has **no imap branch** — an `imap` value falls through to
// `_detect_source`, which would run feed detection against an `imap://` URL and
// store a nonsense `page` source. Mailboxes go through `POST /me/email-sources`.
// The Literal is misleading and this UI does not trust it.
type SourceKind = "detect" | "paperless";

/** Where a paperless credential is actually written and read.
 *
 *  NOT `/admin/connector-credentials`: that page's Household and System tabs
 *  write tiers `paperless_credentials.load_connection` never consults — it reads
 *  `ccs.get_payload(user_id, PAPERLESS)` and nothing else. Saving there produces
 *  a credential the badge cannot see and the poller can never load.
 *
 *  `MeConnectorsView` is a nested route under `/me`, so the mounted path is
 *  `/me/connectors`. Bare `/connectors` matches nothing and falls through to the
 *  catch-all, which redirects to `/chat`. */
const CREDENTIALS_PATH = "/me/connectors";

export function AddSourcePanel({ onAdded }: { onAdded: () => void }): JSX.Element {
  const { client } = useAuth();
  const [kind, setKind] = useState<SourceKind>("detect");
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [preview, setPreview] = useState<SourcePreview | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [consent, dispatch] = useReducer(consentReducer, initialConsentState);

  // Presence for every connector in one request, refetched when the window
  // regains focus — which is the moment the user comes back from the credentials
  // tab. `staleTime: 0` is stated rather than inherited: the app QueryClient sets
  // only `retry: false`, and this codebase already overrides staleTime per-query
  // where focus refetches are unwanted, so relying on the default would be
  // relying on something a later edit may take away.
  const credentialsQ = useQuery({
    queryKey: CREDENTIALS_KEY,
    queryFn: () => listMyCredentials(client),
    staleTime: 0,
    refetchOnWindowFocus: true,
  });

  const paperlessRow = credentialsQ.data?.items.find((i) => i.connector === "paperless");
  const credentialAt: ConsentState["credentialAt"] = credentialsQ.isError
    ? "unknown"
    : credentialsQ.data
      ? (paperlessRow?.updated_at ?? null)
      : "unknown";

  useEffect(() => {
    dispatch({ type: "credentialRead", at: credentialAt });
  }, [credentialAt]);

  const resetInputs = () => {
    setPreview(null);
    dispatch({ type: "inputsChanged" });
  };

  // LUM-839: `POST /sources` is per-user now, so it carries two refusals the
  // panel did not have to render before. Both are matched on `status`/`code`,
  // never on prose — the file's existing rule.
  const RATE_LIMITED = "Too many attempts. Wait a minute and try again.";
  const capOrRateMessage = (e: unknown): string | null => {
    if (!(e instanceof ApiError)) return null;
    if (e.status === 429) return RATE_LIMITED;
    // `ApiError` surfaces the route's machine-readable `code` directly
    // (client.ts:213) and `detail` carries the server's message.
    if (e.status === 422 && e.code === "source_cap_reached") {
      return e.detail || "You've reached your source limit.";
    }
    return null;
  };

  const previewM = useMutation({
    mutationFn: () =>
      previewSource(client, {
        url,
        ...(kind === "paperless" ? { source_type: "paperless" as const } : {}),
      }),
    onSuccess: (p) => {
      setPreview(p);
      setMsg(null);
    },
    onError: (e: unknown) =>
      // 422 here is the base-URL validator, whose message is safe and specific.
      // Anything else gets a fixed line: the 500 body carries an exception
      // string that can hold a DSN or an internal host.
      // A 429 must be handled BEFORE the generic fallback. The fallback says
      // "try again", which is the precise opposite of the correct instruction
      // for a rate limit — and preview is the path the limiter fires on most.
      setMsg(
        capOrRateMessage(e) ??
          (e instanceof ApiError && e.status === 422
            ? "That address doesn't look valid — check it and try again."
            : "We couldn't check that address — try again."),
      ),
  });

  const addM = useMutation({
    mutationFn: () => {
      dispatch({ type: "submitted" });
      return createSource(client, {
        url,
        ...(name ? { name } : {}),
        ...(category ? { category } : {}),
        ...(kind === "paperless" ? { source_type: "paperless" as const } : {}),
        // Exactly the flags true in `acked`, assembled in one place so the view
        // cannot drift from the rule.
        ...consentFlags(consent),
      });
    },
    onSuccess: () => {
      dispatch({ type: "succeeded" });
      setUrl("");
      setName("");
      setCategory("");
      setPreview(null);
      setMsg("Source added.");
      onAdded();
    },
    onError: (e: unknown) => {
      dispatch({ type: "refused", error: e });
      // Ask the PARSERS whether this is a refusal the user can answer — not the
      // raw body. An earlier version tested the body for the string `"code"`,
      // which is true of every coded 422 including ones neither parser matches,
      // so an unrecognised refusal rendered no card and no message: precisely
      // the "runs and shows nothing" defect these gates exist to prevent, and
      // it was caught by the test written for that property.
      const capOrRate = capOrRateMessage(e);
      if (capOrRate !== null) {
        setMsg(capOrRate);
        return;
      }
      const recognised = starvationWarning(e) !== null || paperlessOverlapWarning(e) !== null;
      setMsg(recognised ? null : "We couldn't add that source — try again.");
    },
  });

  const busy = previewM.isPending || addM.isPending;
  const card = consent.shown;

  return (
    <section aria-labelledby="add-source-heading">
      <h2 id="add-source-heading">Add a source</h2>

      <p>
        <label htmlFor="source-kind">What are you adding?</label>{" "}
        <select
          id="source-kind"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as SourceKind);
            resetInputs();
          }}
        >
          {/* Two options, never three. See the note on `SourceKind`. */}
          <option value="detect">Detect automatically</option>
          <option value="paperless">Paperless</option>
        </select>
      </p>

      {kind === "paperless" ? (
        <PaperlessCredentialStep credentialAt={credentialAt} />
      ) : null}

      <p>
        <label htmlFor="source-url">
          {kind === "paperless" ? "Paperless address" : "Address"}
        </label>{" "}
        <input
          id="source-url"
          type="url"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            resetInputs();
          }}
          placeholder="https://…"
        />
      </p>

      {/* Never on keystroke: the preview is a live outbound fetch that can probe
          several paths off the site root, bounded only by httpx timeouts. */}
      <p>
        <button type="button" onClick={() => previewM.mutate()} disabled={!url || busy}>
          Check
        </button>{" "}
        {previewM.isPending ? (
          <>
            <span role="status" aria-label="Source check progress">Checking…</span>{" "}
            <button type="button" onClick={() => previewM.reset()}>
              Cancel
            </button>
          </>
        ) : null}
      </p>

      {preview ? (
        <PreviewPanel preview={preview} kind={kind} />
      ) : null}

      {preview ? (
        <>
          <p>
            <label htmlFor="source-name">Name (optional)</label>{" "}
            <input id="source-name" value={name} onChange={(e) => setName(e.target.value)} />
          </p>
          <p>
            <label htmlFor="source-category">Category (optional)</label>{" "}
            <input
              id="source-category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            />
          </p>
          <p>
            {/* "Add" pays the same N-probe cost as "Check": POST /sources is
                stateless and re-runs detection before it looks at `confirm`. */}
            <button type="button" onClick={() => addM.mutate()} disabled={busy}>
              Add
            </button>{" "}
            {addM.isPending ? (
              <>
                <span role="status" aria-label="Source add progress">Adding…</span>{" "}
                <button type="button" onClick={() => addM.reset()}>
                  Cancel
                </button>
              </>
            ) : null}
          </p>
        </>
      ) : null}

      {msg ? <p role="status" aria-label="Source add result">{msg}</p> : null}

      {card ? (
        <ConsentCard
          card={card}
          busy={busy}
          onAcknowledge={() => {
            dispatch({ type: "acknowledged" });
            // The acknowledgement is a mutation parameter, not state read at
            // call time — the re-post is what sends it, and only this press
            // causes one. A component that always sent `true` would turn the
            // gate into a dismissible banner in JSON.
            setTimeout(() => addM.mutate(), 0);
          }}
        />
      ) : null}
    </section>
  );
}

/** Step one of D4's "one flow, two visible steps". It is a *state on this
 *  screen*, not a link away from it — which is why the badge exists at all. */
function PaperlessCredentialStep({
  credentialAt,
}: {
  credentialAt: ConsentState["credentialAt"];
}): JSX.Element | null {
  // A failed presence read blocks nothing and says nothing: a transient blip in
  // the credential service must not stop someone creating a source they are
  // entitled to, and an error about a credential they may already have is noise.
  if (credentialAt === "unknown") return null;

  const present = credentialAt !== null;
  return (
    <p>
      <strong>{present ? "Paperless credentials saved." : "No paperless credentials yet."}</strong>{" "}
      {present ? null : (
        <>Without them this source will be created but cannot poll. </>
      )}
      {/* A new tab, so step one does not destroy step two: in-app navigation
          unmounts this panel and takes the form, the preview and any
          acknowledgements with it — and fires no window-focus event to refetch
          with afterwards. */}
      <a href={CREDENTIALS_PATH} target="_blank" rel="noopener">
        {present ? "Change them" : "Add them"}
      </a>
    </p>
  );
}

function PreviewPanel({
  preview,
  kind,
}: {
  preview: SourcePreview;
  kind: SourceKind;
}): JSX.Element {
  if (kind === "paperless") {
    // The paperless preview validates the URL's shape and stops: it never
    // contacts the instance, never uses the credential, and always returns an
    // empty `preview_items`. So the copy claims exactly that and no more — not
    // "connected", not "verified", not "working" — and an empty list here is the
    // expected state, never rendered as "no items found".
    return (
      <div>
        <p>The address looks valid. We haven&apos;t contacted your Paperless server yet.</p>
        <p>Adding it will start polling; the sources list will show if it can&apos;t connect.</p>
      </div>
    );
  }
  if (preview.reachable === false) {
    // LUM-841: the fetch failed, so this is a typo or a host that is down —
    // not a page with no feed. Saying "we'll watch the page" here is the
    // reassuring lie that sent people away with a source that polls nothing.
    // Add stays available: a host can be briefly down and the user may know
    // better than the probe.
    return (
      <div role="alert">
        <p>We couldn&apos;t reach that address. Check it for a typo.</p>
        <p>
          You can still add it — if the server was just temporarily down, it will start
          working on its own. The sources list shows anything that keeps failing.
        </p>
      </div>
    );
  }
  return (
    <div>
      <p>
        Detected: <strong>{preview.source_type}</strong>
        {preview.url && preview.url !== "" ? <> — {preview.url}</> : null}
      </p>
      {preview.source_type === "page" && preview.preview_items.length === 0 ? (
        // Reachable, but nothing extractable: a page IS a valid source, and
        // this really is the "no feed found" case the copy claims.
        <p>No feed found, so we&apos;ll watch the page itself for changes.</p>
      ) : (
        <ul>
          {preview.preview_items.slice(0, 3).map((item, i) => (
            <li key={i}>{item.title ?? item.url ?? "(untitled)"}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Each arm renders through its own shipped parser and keeps its own
 *  acknowledgement. Never one combined "I understand": the two arms warn about
 *  opposite harms — paperless missing mail, versus documents filed twice — so a
 *  single control would mean accepting either silently accepts the other. */
function ConsentCard({
  card,
  busy,
  onAcknowledge,
}: {
  card: NonNullable<ConsentState["shown"]>;
  busy: boolean;
  onAcknowledge: () => void;
}): JSX.Element {
  const isOverlap = card.arm === "overlap";
  const message = card.warning.message || (isOverlap ? OVERLAP_FALLBACK : "");
  return (
    <div role="alert">
      <h3>{isOverlap ? "Paperless may file these twice" : "Paperless may miss some mail"}</h3>
      <p>{message}</p>
      {isOverlap && card.warning.mailboxes.length > 0 ? (
        <ul>
          {card.warning.mailboxes.map((m) => (
            <li key={m.source_name}>
              {m.source_name} — {m.rule_count} {m.rule_count === 1 ? "rule" : "rules"}
            </li>
          ))}
        </ul>
      ) : null}
      {card.warning.alternative ? <p>{card.warning.alternative}</p> : null}
      {card.warning.recovery ? <p>{card.warning.recovery}</p> : null}
      <button type="button" onClick={onAcknowledge} disabled={busy}>
        Connect anyway
      </button>
    </div>
  );
}
