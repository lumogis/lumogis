// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-644 Email Setup v2 — guided mailbox on-ramp: provider pick + app-password
// guidance, a live connection test before save, import scopes, and opt-in
// folder-derived (Lumogis-internal) labels.
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { InfoTip } from "@/components/page/InfoTip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { ChevronDown } from "lucide-react";
import { useAuth } from "../../auth/AuthProvider";
import { describeMailboxSaveFailure } from "./mailboxSaveMessage";
import { describeSendOptInFailure, SEND_OPT_IN_PREFIX } from "./sendOptInMessage";
import {
  createEmailSource,
  listEmailProviders,
  putImapCredentials,
  setEmailSmtp,
  testEmailSource,
  type EmailProbeResponse,
  type ImportScope,
  type ProbeFolder,
  type ProviderInfo,
} from "../../api/emailSources";
import { getTaxonomy, putTaxonomy } from "../../api/meEmailLabels";
import { OAuthDevicePanel } from "./OAuthDevicePanel";

const LABEL_RE = /^[a-z0-9_-]{1,64}$/;
// LUM-687: a provider offers OAuth device-flow connect only when the backend
// advertises it for THIS install (BYO client_id configured) — never inferred
// from auth_status alone.
const supportsOAuth = (p: ProviderInfo): boolean => !!p.supports_oauth;

// "Other" is a fixed manual-entry card, not a registry row.
const OTHER: ProviderInfo = {
  id: "other",
  display_name: "Other (manual)",
  imap_host: "",
  imap_port: 993,
  smtp_host: "",
  smtp_port: 587,
  app_password_url: "",
  help_steps: [],
  auth_status: "app_password",
  enabled: true,
  disabled_reason: null,
  notes: "Enter your provider's IMAP host manually.",
};

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

// LUM-644 (app-password) / LUM-691 (OAuth parity) — shared "how much history to
// import" fieldset. Both connect paths write to the SAME top-level form state,
// so this is a pure presentational extraction (no duplicated state).
function ImportScopePicker({
  scope,
  since,
  before,
  onScope,
  onSince,
  onBefore,
}: {
  scope: ImportScope;
  since: string;
  before: string;
  onScope: (s: ImportScope) => void;
  onSince: (v: string) => void;
  onBefore: (v: string) => void;
}): JSX.Element {
  const options: { value: ImportScope; label: string }[] = [
    { value: "just_new", label: "Just new mail" },
    { value: "last_7d", label: "Last 7 days" },
    { value: "last_30d", label: "Last 30 days" },
    { value: "custom", label: "Custom date range" },
  ];
  return (
    <fieldset className="m-0 grid gap-2 rounded-lg border p-3">
      <legend className="px-1 text-sm font-medium">How much history to import</legend>
      <RadioGroup value={scope} onValueChange={(v) => onScope(v as ImportScope)}>
        {options.map((o) => (
          <Label key={o.value}>
            <RadioGroupItem value={o.value} />
            {o.label}
          </Label>
        ))}
      </RadioGroup>
      {scope === "custom" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Label className="grid gap-1.5">
            From
            <Input
              type="date"
              aria-label="Import from date"
              value={since}
              onChange={(e) => onSince(e.target.value)}
              required
            />
          </Label>
          <Label className="grid gap-1.5">
            To
            <Input
              type="date"
              aria-label="Import to date"
              value={before}
              onChange={(e) => onBefore(e.target.value)}
            />
          </Label>
          <p className="m-0 text-sm text-muted-foreground sm:col-span-2">
            Already-imported emails in this range are skipped — no duplicates.
          </p>
        </div>
      ) : null}
    </fieldset>
  );
}

export function EmailMailboxSetupForm({ onCreated }: { onCreated?: () => void }): JSX.Element {
  const { client } = useAuth();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [provider, setProvider] = useState<ProviderInfo | null>(null);

  const [host, setHost] = useState("");
  const [mailboxLabel, setMailboxLabel] = useState("personal");
  const [displayName, setDisplayName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const [testing, setTesting] = useState(false);
  const [probe, setProbe] = useState<EmailProbeResponse | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  const [seedLabels, setSeedLabels] = useState(true);
  const [selectedFolders, setSelectedFolders] = useState<Record<string, boolean>>({});

  const [importScope, setImportScope] = useState<ImportScope>("last_30d");
  const [since, setSince] = useState("");
  const [before, setBefore] = useState("");

  // LUM-650 — "let Lumogis send as me" opt-in (default OFF). From is always this
  // mailbox's account; the compose/reply UX itself is LUM-652. Reuses the
  // mailbox password by default; a separate SMTP app-password is optional. For an
  // "Other" mailbox the submission host/port must be entered (no registry entry).
  const [sendEnabled, setSendEnabled] = useState(false);
  const [sendReusePassword, setSendReusePassword] = useState(true);
  const [sendPassword, setSendPassword] = useState("");
  const [sendSmtpHost, setSendSmtpHost] = useState("");
  const [sendSmtpPort, setSendSmtpPort] = useState("587");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [labelError, setLabelError] = useState<string | null>(null);

  // LUM-687 — OAuth device-flow connect (Outlook). Once the user has entered the
  // mailbox address + label we mount the polling panel.
  const [oauthConnecting, setOauthConnecting] = useState(false);
  // LUM-758: null = a mailbox added now will sync on its own. Otherwise the reason
  // an operator still has to act, so setup can say so up front instead of leaving
  // someone to work it out from a mailbox that silently never syncs.
  const [operatorReason, setOperatorReason] = useState<string | null>(null);
  // LUM-759 — why the send opt-in didn't take, when it didn't. Non-blocking.
  const [sendNotice, setSendNotice] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    listEmailProviders(client)
      .then((r) => {
        if (!live) return;
        setProviders(r.providers);
        // LUM-758: warn BEFORE they finish, not after nothing syncs.
        setOperatorReason(r.new_mailbox_needs_operator ? (r.operator_action_reason ?? "") : null);
      })
      .catch(() => {
        if (live) setProviders([]);
      });
    return () => {
      live = false;
    };
  }, [client]);

  const seedFolders = useMemo(
    () => (probe?.folders ?? []).filter((f) => !f.hidden),
    [probe],
  );

  const pickProvider = (p: ProviderInfo) => {
    if (!p.enabled) return;
    setProvider(p);
    setHost(p.imap_host);
    setProbe(null);
    setTestError(null);
    setOauthConnecting(false);
  };

  // Editing credentials after a test invalidates it — clear the probe so Save is
  // re-gated on a fresh test (never save untested credentials).
  const invalidateTest = () => {
    if (probe !== null || testError !== null) {
      setProbe(null);
      setTestError(null);
    }
  };

  // Save is gated on a successful test, OR a proxy-unverified soft-save (the live
  // test is a Layer-1 feature; proxied deployments verify on first poll).
  const canSave = probe !== null && (probe.ok || probe.proxy_unverified);

  const runTest = async () => {
    setTestError(null);
    setProbe(null);
    setTesting(true);
    try {
      const result = await testEmailSource(client, {
        host: host.trim(),
        port: provider?.imap_port ?? 993,
        username: username.trim(),
        password,
        use_ssl: true,
        auth_type: "app_password",
      });
      setProbe(result);
      const sel: Record<string, boolean> = {};
      for (const f of result.folders) if (!f.hidden) sel[f.name] = f.selected;
      setSelectedFolders(sel);
      if (!result.ok && !result.proxy_unverified) {
        setTestError(result.error ?? "Couldn't connect to this mailbox.");
      }
    } catch (err) {
      // LUM-770: the same defect as the submit catch below, in the same form,
      // which the ticket did not name. `String(err.detail)` is the serialised
      // response body. Same closed-set treatment; the fallback differs because
      // this is the connection test, not the save.
      setTestError(describeMailboxSaveFailure(err, "Couldn't test this mailbox. Try again."));
    } finally {
      setTesting(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    // LUM-759 — same reset as `error` above. Without it a notice from a failed
    // attempt survives a later SUCCESSFUL one, so the user reads "Sending isn't
    // on yet" about a mailbox where sending is now on. Found at /verify-plan by
    // code review, not by any test.
    setSendNotice(null);
    if (!LABEL_RE.test(mailboxLabel)) {
      setLabelError("Mailbox label must match [a-z0-9_-]{1,64}");
      return;
    }
    setLabelError(null);
    setBusy(true);
    try {
      const created = await createEmailSource(client, {
        host: host.trim(),
        port: provider?.imap_port ?? 993,
        mailbox_label: mailboxLabel,
        name: displayName.trim() || undefined,
        import_scope: importScope,
        import_since: importScope === "custom" ? since || undefined : undefined,
        import_before: importScope === "custom" && before ? before : undefined,
      });
      await putImapCredentials(client, created.source_id, {
        host: host.trim(),
        port: provider?.imap_port ?? 993,
        username: username.trim(),
        password,
        use_ssl: true,
        auth_type: "app_password",
      });
      // LUM-650 — opt-in to sending. Non-blocking: the mailbox is connected for
      // reading regardless of whether the send opt-in succeeds.
      if (sendEnabled) {
        const isOther = provider?.id === "other";
        try {
          await setEmailSmtp(client, created.source_id, {
            enabled: true,
            reuse_imap_password: sendReusePassword,
            app_password: sendReusePassword ? undefined : sendPassword,
            smtp_host: isOther ? sendSmtpHost.trim() || undefined : undefined,
            smtp_port: isOther ? Number(sendSmtpPort) || undefined : undefined,
          });
        } catch (err) {
          // Still non-blocking — the mailbox is connected for reading either
          // way, which is what the previous empty `catch {}` was protecting.
          // What changes (LUM-759) is that the user is told sending did not
          // switch on, and why.
          // LUM-769: the prefix is passed EXPLICITLY now that a second surface
          // shares this module. Relying on the default would make this
          // surface's framing invisible at the call site and silently
          // wrong if the default ever moved.
          setSendNotice(describeSendOptInFailure(err, SEND_OPT_IN_PREFIX));
        }
      }
      // Opt-in folder-derived labels — these are Lumogis's own categories, written
      // via the existing taxonomy endpoint. Editing them never touches the mailbox.
      if (seedLabels && probe) {
        // Never overwrite an existing category (would silently rewrite its label/
        // sort or re-enable a disabled one), and never send duplicate slugs (two
        // folders can slugify the same, which 422s the whole batch).
        let existing = new Set<string>();
        try {
          const tax = await getTaxonomy(client);
          existing = new Set((tax.categories ?? []).map((c) => c.slug));
        } catch {
          // If we can't read the taxonomy, still de-dupe within the batch below.
        }
        const seen = new Set<string>();
        const categories = seedFolders
          .filter((f) => selectedFolders[f.name])
          .map((f, i) => ({ slug: slugify(f.name), label: f.name, enabled: true, sort: i }))
          .filter((c) => c.slug.length > 0)
          .filter((c) => {
            if (existing.has(c.slug) || seen.has(c.slug)) return false;
            seen.add(c.slug);
            return true;
          });
        if (categories.length > 0) {
          try {
            await putTaxonomy(client, { categories });
          } catch {
            // Labels are non-blocking: the mailbox is connected regardless.
          }
        }
      }
      setPassword("");
      setSendPassword("");
      onCreated?.();
    } catch (err) {
      // LUM-770: was `String(err.detail)` — the serialised response body, and on
      // any route that passes an exception message through, a credential-layer
      // string naming a host, a payload field or a user_id. Keyed on `code`
      // against a closed set now, exactly as the sibling send-opt-in notice has
      // been since ADR-197.
      setError(describeMailboxSaveFailure(err));
    } finally {
      setBusy(false);
    }
  };

  const allProviders = useMemo(() => [...providers, OTHER], [providers]);

  // Step 1 — provider pick.
  if (!provider) {
    return (
      <div className="mb-4 grid max-w-2xl gap-3">
        <div>
          <h3 className="m-0 text-base font-semibold">Connect a mailbox</h3>
          <p className="m-0 mt-1 text-sm text-muted-foreground">
            Pick your email provider to get set-up steps.
          </p>
        </div>
        {operatorReason !== null ? (
          <Alert data-testid="operator-action-notice" role="status">
            <AlertDescription>
              {operatorReason === "ceiling"
                ? "Heads up: this system restricts outbound connections at start-up, " +
                  "so a new mailbox won't sync until an admin restarts Lumogis. You can " +
                  "finish setting it up now — ask an admin to restart when you're done."
                : "Heads up: automatic updates to the outbound connection rules are " +
                  "switched off on this system, so a new mailbox won't sync until an " +
                  "admin applies the change. You can finish setting it up now."}
            </AlertDescription>
          </Alert>
        ) : null}
        <div className="grid gap-2 sm:grid-cols-2">
          {allProviders.map((p) => (
            <div key={p.id} className="grid content-start gap-1">
              <Button
                type="button"
                variant="outline"
                className="h-auto w-full justify-start py-2"
                disabled={!p.enabled}
                title={p.enabled ? p.notes : undefined}
                onClick={() => pickProvider(p)}
              >
                {p.display_name}
              </Button>
              {/* The reason (a docs path) is not main text: the plain sentence is,
                  and the path sits behind the (i) for whoever does the setup. */}
              {!p.enabled ? (
                <p className="m-0 flex items-center gap-1 text-sm text-muted-foreground">
                  Needs a one-time setup by the household admin.
                  {p.disabled_reason ? (
                    <InfoTip about={p.display_name}>{p.disabled_reason}</InfoTip>
                  ) : null}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Step 2 (OAuth providers) — collect the mailbox address + label, then hand off
  // to the device-flow polling panel. No app password is ever entered here.
  if (supportsOAuth(provider)) {
    const startOAuth = () => {
      if (!LABEL_RE.test(mailboxLabel)) {
        setLabelError("Mailbox label must match [a-z0-9_-]{1,64}");
        return;
      }
      // Mirrors the backend's OAuthDeviceStartRequest validator (custom needs a
      // start date) — the OAuth branch isn't a <form>, so there's no native
      // required-field submit gate to lean on here.
      if (importScope === "custom" && !since) {
        setLabelError("Custom date range needs a start date");
        return;
      }
      setLabelError(null);
      setOauthConnecting(true);
    };
    return (
      <div className="mb-4 grid max-w-2xl gap-3">
        <h3 className="m-0 text-base font-semibold">Connect {provider.display_name}</h3>
        <div>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setProvider(null);
              setOauthConnecting(false);
            }}
          >
            ← Change provider
          </Button>
        </div>
        {oauthConnecting ? (
          <OAuthDevicePanel
            client={client}
            provider={provider.id}
            providerLabel={provider.display_name}
            email={username.trim()}
            mailboxLabel={mailboxLabel}
            importScope={importScope}
            importSince={importScope === "custom" ? since || undefined : undefined}
            importBefore={importScope === "custom" && before ? before : undefined}
            onConnected={() => onCreated?.()}
            onCancel={() => setOauthConnecting(false)}
          />
        ) : (
          <>
            <p className="m-0 text-sm text-muted-foreground">
              Sign in to {provider.display_name} in your browser — no app password needed.
              Enter your mailbox address and a label, then continue.
            </p>
            <Label className="grid gap-1.5">
              Email address
              <Input
                type="email"
                aria-label="Email address"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </Label>
            <Label className="grid gap-1.5">
              Mailbox label
              <Input
                aria-label="Mailbox label"
                value={mailboxLabel}
                onChange={(e) => setMailboxLabel(e.target.value)}
                required
              />
            </Label>
            {labelError ? (
              <Alert variant="destructive">
                <AlertDescription>{labelError}</AlertDescription>
              </Alert>
            ) : null}
            <ImportScopePicker
              scope={importScope}
              since={since}
              before={before}
              onScope={setImportScope}
              onSince={setSince}
              onBefore={setBefore}
            />
            <div>
              <Button type="button" onClick={startOAuth} disabled={!username.trim()}>
                Continue to {provider.display_name} sign-in
              </Button>
            </div>
          </>
        )}
      </div>
    );
  }

  // Step 2 — credentials, test, options.
  return (
    <form className="mb-4 grid max-w-2xl gap-3" onSubmit={(e) => void submit(e)}>
      <h3 className="m-0 text-base font-semibold">Connect {provider.display_name}</h3>
      <div>
        <Button type="button" variant="ghost" onClick={() => setProvider(null)}>
          ← Change provider
        </Button>
      </div>

      {provider.help_steps.length > 0 ? (
        <div className="grid gap-2 text-sm">
          <p className="m-0">To connect {provider.display_name}, create an app password:</p>
          <ol className="m-0 grid gap-1 pl-5">
            {provider.help_steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
          {provider.app_password_url ? (
            <a
              className="text-accent-text underline-offset-4 hover:underline"
              href={provider.app_password_url}
              target="_blank"
              rel="noreferrer noopener"
            >
              Open {provider.display_name} app passwords
            </a>
          ) : null}
        </div>
      ) : null}

      {provider.id === "other" ? (
        <Label className="grid gap-1.5">
          IMAP host
          <Input
            value={host}
            onChange={(e) => {
              setHost(e.target.value);
              invalidateTest();
            }}
            required
          />
        </Label>
      ) : null}

      <Label className="grid gap-1.5">
        Mailbox label
        <Input
          aria-label="Mailbox label"
          value={mailboxLabel}
          onChange={(e) => setMailboxLabel(e.target.value)}
          required
        />
      </Label>
      {labelError ? (
        <Alert variant="destructive">
          <AlertDescription>{labelError}</AlertDescription>
        </Alert>
      ) : null}
      <Label className="grid gap-1.5">
        Display name (optional)
        <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </Label>
      <Label className="grid gap-1.5">
        Username
        <Input
          value={username}
          onChange={(e) => {
            setUsername(e.target.value);
            invalidateTest();
          }}
          required
        />
      </Label>
      <Label className="grid gap-1.5">
        App password
        <Input
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            invalidateTest();
          }}
          type="password"
          autoComplete="new-password"
          required
          minLength={1}
        />
      </Label>

      <div>
        <Button
          type="button"
          variant="outline"
          onClick={() => void runTest()}
          disabled={testing || !username || !password}
        >
          {testing ? "Testing…" : "Test connection"}
        </Button>
      </div>

      {probe && probe.ok ? (
        <p role="status" className="m-0 text-sm">
          Connected
          {probe.message_count != null ? ` — ${probe.message_count} messages in your inbox` : ""}.
        </p>
      ) : null}
      {probe && probe.proxy_unverified ? (
        <p role="status" className="m-0 text-sm">
          Couldn't verify on this deployment; your mailbox will be verified on the first
          sync. You can still save.
        </p>
      ) : null}
      {testError ? (
        <Alert variant="destructive">
          <AlertDescription>{testError}</AlertDescription>
        </Alert>
      ) : null}

      {probe && probe.ok && seedFolders.length > 0 ? (
        <fieldset className="m-0 grid gap-2 rounded-lg border p-3">
          <legend className="px-1 text-sm font-medium">Start my labels from my mailbox folders</legend>
          <Label>
            <Checkbox checked={seedLabels} onCheckedChange={(on: boolean) => setSeedLabels(on)} />
            Use my folder names as a starting point
          </Label>
          <p className="m-0 text-sm text-muted-foreground">
            These are <strong>Lumogis labels</strong> — Lumogis's own view. Editing them
            won't change your mailbox.
          </p>
          {seedLabels
            ? seedFolders.map((f: ProbeFolder) => (
                <Label key={f.name}>
                  <Checkbox
                    checked={!!selectedFolders[f.name]}
                    onCheckedChange={(on: boolean) =>
                      setSelectedFolders((s) => ({ ...s, [f.name]: on }))
                    }
                  />
                  {f.name}
                </Label>
              ))
            : null}
        </fieldset>
      ) : null}

      <ImportScopePicker
        scope={importScope}
        since={since}
        before={before}
        onScope={setImportScope}
        onSince={setSince}
        onBefore={setBefore}
      />

      <fieldset className="m-0 grid gap-3 rounded-lg border p-3">
        <legend className="px-1 text-sm font-medium">Sending</legend>
        <Label>
          <Checkbox checked={sendEnabled} onCheckedChange={(on: boolean) => setSendEnabled(on)} />
          Let Lumogis send as me (reply &amp; compose)
        </Label>
        {sendEnabled ? (
          <div className="grid gap-3">
            <p className="m-0 text-sm text-muted-foreground">
              Emails are sent from <strong>{username.trim() || "this mailbox"}</strong> through
              your provider — never a Lumogis address. A send to a new address always asks first.
            </p>
            <Label>
              <Checkbox
                checked={sendReusePassword}
                onCheckedChange={(on: boolean) => setSendReusePassword(on)}
              />
              Use my mailbox password for sending
            </Label>
            {!sendReusePassword ? (
              <Label className="grid gap-1.5">
                Separate app password for sending
                <Input
                  type="password"
                  aria-label="SMTP app password"
                  value={sendPassword}
                  onChange={(e) => setSendPassword(e.target.value)}
                  autoComplete="off"
                  /* LUM-775: this field renders only when the user has unticked
                     "use my mailbox password", so it is unconditionally required
                     when present. Deliberately NOT trimmed — a sending password
                     may legitimately contain spaces. */
                  required
                />
              </Label>
            ) : null}
            {provider?.id === "other" ? (
              <div className="grid gap-3 sm:grid-cols-[1fr_8rem]">
                <Label className="grid gap-1.5">
                  SMTP host
                  <Input
                    type="text"
                    aria-label="SMTP host"
                    value={sendSmtpHost}
                    /* LUM-775: trimmed on entry, because `required` alone does not
                       reject a whitespace-only value — and submit sends
                       `sendSmtpHost.trim() || undefined`, so " " reached the server
                       as a MISSING host and came back 422. A hostname never
                       contains spaces, so trimming here loses nothing. */
                    onChange={(e) => setSendSmtpHost(e.target.value.trim())}
                    placeholder="smtp.example.com"
                    required
                  />
                </Label>
                <Label className="grid gap-1.5">
                  SMTP port
                  <Input
                    type="number"
                    aria-label="SMTP port"
                    value={sendSmtpPort}
                    onChange={(e) => setSendSmtpPort(e.target.value)}
                    required
                  />
                </Label>
              </div>
            ) : (
              // Developer detail (host, port) stays available, one click away.
              <Collapsible>
                <CollapsibleTrigger render={<Button type="button" variant="ghost" size="sm" />}>
                  <ChevronDown aria-hidden="true" />
                  Details
                </CollapsibleTrigger>
                <CollapsibleContent keepMounted>
                  <p className="m-0 mt-2 text-sm text-muted-foreground">
                    Using {provider?.smtp_host || "your provider's server"} (port{" "}
                    {provider?.smtp_port ?? 587}).
                  </p>
                </CollapsibleContent>
              </Collapsible>
            )}
          </div>
        ) : null}
      </fieldset>

      <p className="m-0 text-sm text-muted-foreground">
        Lumogis reads message headers only (never message bodies unless you enable it),
        and never sends anything without asking.
      </p>

      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {/* LUM-759 — the send opt-in used to fail into an empty `catch {}`, so a
          user who ticked "let Lumogis send as me" got a connected mailbox with
          sending silently off and no message. Still non-blocking (`role=status`,
          not `alert`) — the mailbox IS connected either way, which is what the
          empty catch was protecting. Matches the LUM-758 operator notice above. */}
      {sendNotice ? (
        <p role="status" data-testid="send-optin-notice" className="m-0 text-sm">
          {sendNotice}
        </p>
      ) : null}
      <div>
        <Button type="submit" disabled={busy || !canSave}>
          {busy ? "Adding…" : "Add mailbox"}
        </Button>
      </div>
    </form>
  );
}
