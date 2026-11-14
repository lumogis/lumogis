// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-644 Email Setup v2 — guided flow: provider pick, live test gates Save,
// disabled Outlook, import-scope custom reveal, proxy-unverified soft-save.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { EmailMailboxSetupForm } from "../../../src/features/signals/EmailMailboxSetupForm";
import type { ProviderInfo } from "../../../src/api/emailSources";
import { jsonResponse } from "../../helpers/jsonResponse";

const GMAIL: ProviderInfo = {
  id: "gmail", display_name: "Gmail", imap_host: "imap.gmail.com", imap_port: 993,
  smtp_host: "smtp.gmail.com", smtp_port: 587,
  app_password_url: "https://myaccount.google.com/apppasswords",
  help_steps: ["Turn on 2FA", "Create an app password"],
  auth_status: "app_password", enabled: true, disabled_reason: null, notes: "",
  supports_oauth: false, oauth_provider: null,
};

// LUM-687: Outlook is now an OAuth device-flow provider. On a CONFIGURED install
// the /me/email-providers route returns it enabled with supports_oauth=true; an
// unconfigured install returns it disabled-with-reason (no dead-end).
const OUTLOOK_ENABLED: ProviderInfo = {
  id: "outlook", display_name: "Outlook.com", imap_host: "outlook.office365.com",
  imap_port: 993, smtp_host: "smtp.office365.com", smtp_port: 587, app_password_url: "",
  help_steps: [], auth_status: "oauth_only", enabled: true, disabled_reason: null, notes: "",
  supports_oauth: true, oauth_provider: "microsoft",
};
const OUTLOOK_DISABLED: ProviderInfo = {
  ...OUTLOOK_ENABLED, enabled: false, supports_oauth: false, oauth_provider: null,
  disabled_reason: "Connecting Outlook needs a one-time OAuth app setup — see docs/operators/oauth-email-setup.md.",
};

const PROVIDERS = { providers: [GMAIL, OUTLOOK_ENABLED] };
const PROVIDERS_OUTLOOK_DISABLED = { providers: [GMAIL, OUTLOOK_DISABLED] };

function renderForm(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  return render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <EmailMailboxSetupForm onCreated={() => undefined} />
    </AuthProvider>,
  );
}

interface ProbeOverride {
  ok?: boolean;
  message_count?: number | null;
  folders?: { name: string; role: string | null; hidden: boolean; selected: boolean }[];
  proxy_unverified?: boolean;
  error?: string | null;
}

function makeFetch(
  opts: {
    probe?: ProbeOverride;
    calls?: { url: string; method?: string }[];
    // LUM-758: the response also carries whether an operator must still act.
    providers?: {
      providers: ProviderInfo[];
      new_mailbox_needs_operator?: boolean;
      operator_action_reason?: string | null;
    };
    // LUM-687 device-flow: poll status returned by .../device/poll.
    pollStatus?: string;
    pollSourceId?: string;
    pollVerified?: boolean;
  } = {},
) {
  const calls = opts.calls ?? [];
  return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const u = String(input);
    calls.push({ url: u, method: init?.method });
    if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
    if (u.includes("/api/v1/me/email-providers")) return jsonResponse(200, opts.providers ?? PROVIDERS);
    if (u.includes("/oauth/") && u.includes("/device/start") && init?.method === "POST") {
      return jsonResponse(200, {
        flow_id: "F1", user_code: "WXYZ-1234",
        verification_uri: "https://ms.example/dev",
        verification_uri_complete: "https://ms.example/dev?otc=WXYZ-1234",
        interval: 1, expires_in: 900,
      });
    }
    if (u.includes("/oauth/") && u.includes("/device/poll") && init?.method === "POST") {
      return jsonResponse(200, {
        status: opts.pollStatus ?? "connected",
        source_id: opts.pollSourceId ?? "src-oauth",
        verified: opts.pollVerified ?? true,
        error: null,
      });
    }
    if (u.includes("/me/email-sources/test") && init?.method === "POST") {
      return jsonResponse(200, {
        ok: opts.probe?.ok ?? true, message_count: opts.probe?.message_count ?? 12,
        folders: opts.probe?.folders ?? [{ name: "Receipts", role: null, hidden: false, selected: true }],
        capabilities: ["IMAP4REV1"], error: opts.probe?.error ?? null,
        proxy_unverified: opts.probe?.proxy_unverified ?? false,
      });
    }
    if (u.includes("/me/email-sources") && init?.method === "POST") {
      return jsonResponse(201, {
        status: "created", source_id: "src-1", source_type: "imap",
        url: "imap://imap.gmail.com:993/personal", extraction_method: "imap_metadata",
      });
    }
    if (u.includes("/connector-credentials/imap") && init?.method === "PUT") {
      return jsonResponse(200, { connector: "imap", instance: "src-1" });
    }
    if (u.includes("/api/v1/me/email-taxonomy") && init?.method === "PUT") {
      return jsonResponse(200, { categories: [] });
    }
    if (u.includes("/smtp") && init?.method === "PUT") {
      return jsonResponse(200, { ok: true, enabled: true, tested: true });
    }
    return jsonResponse(404, {});
  }) as unknown as typeof fetch;
}

async function pickGmailAndFill() {
  const u = userEvent.setup();
  await waitFor(() => expect(screen.getByRole("button", { name: /^Gmail/ })).toBeInTheDocument());
  await u.click(screen.getByRole("button", { name: /^Gmail/ }));
  await u.type(screen.getByLabelText(/^username/i), "alice@gmail.com");
  await u.type(screen.getByLabelText(/app password/i), "app-pw");
  return u;
}

describe("EmailMailboxSetupForm (guided)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows Outlook disabled-with-reason when OAuth is not configured on the server", async () => {
    renderForm(makeFetch({ providers: PROVIDERS_OUTLOOK_DISABLED }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^Gmail/ })).toBeInTheDocument());
    const outlook = screen.getByRole("button", { name: /Outlook/ });
    expect(outlook).toBeDisabled();
    expect(outlook).toHaveTextContent(/one-time OAuth app setup/i);
  });

  it("gates Save behind a successful test", async () => {
    renderForm(makeFetch());
    const u = await pickGmailAndFill();
    // Before testing, Add mailbox is disabled.
    expect(screen.getByRole("button", { name: /add mailbox/i })).toBeDisabled();
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/12 messages/i));
    expect(screen.getByRole("button", { name: /add mailbox/i })).not.toBeDisabled();
  });

  it("tests then creates source + stores credentials", async () => {
    const calls: { url: string; method?: string }[] = [];
    renderForm(makeFetch({ calls }));
    const u = await pickGmailAndFill();
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/me/email-sources") && c.method === "POST")).toBe(true),
    );
    expect(calls.some((c) => c.url.includes("instance=src-1") && c.method === "PUT")).toBe(true);
  });

  it("reveals custom date inputs with a skip-duplicates note", async () => {
    renderForm(makeFetch());
    const u = await pickGmailAndFill();
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    await u.click(screen.getByLabelText(/custom date range/i));
    expect(screen.getByLabelText(/import from date/i)).toBeInTheDocument();
    expect(screen.getByText(/already-imported emails in this range are skipped/i)).toBeInTheDocument();
  });

  it("allows soft-save on a proxy-unverified deployment", async () => {
    renderForm(makeFetch({ probe: { ok: false, proxy_unverified: true, message_count: null, folders: [] } }));
    const u = await pickGmailAndFill();
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/verified on the first sync/i));
    expect(screen.getByRole("button", { name: /add mailbox/i })).not.toBeDisabled();
  });

  // LUM-650 — the "let Lumogis send as me" opt-in (credential opt-in only; the
  // compose/reply UX is LUM-652).
  it("send opt-in is off by default and reveals reuse-password + provider host when enabled", async () => {
    renderForm(makeFetch());
    const u = await pickGmailAndFill();
    const optIn = screen.getByLabelText(/let lumogis send as me/i) as HTMLInputElement;
    expect(optIn).not.toBeChecked();
    // The separate-password field is hidden until the opt-in is on.
    expect(screen.queryByLabelText(/smtp app password/i)).not.toBeInTheDocument();
    await u.click(optIn);
    expect(screen.getByLabelText(/use my mailbox password/i)).toBeChecked();
    // Known provider → host shown from the registry, not asked for.
    expect(screen.getByText(/smtp\.gmail\.com/)).toBeInTheDocument();
    // Switching off reuse reveals the separate app-password field.
    await u.click(screen.getByLabelText(/use my mailbox password/i));
    expect(screen.getByLabelText(/smtp app password/i)).toBeInTheDocument();
  });

  it("enabling send posts the smtp opt-in with reuse_imap_password", async () => {
    const calls: { url: string; method?: string; body?: string }[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      calls.push({ url: u, method: init?.method, body: init?.body as string | undefined });
      if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/api/v1/me/email-providers")) return jsonResponse(200, PROVIDERS);
      if (u.includes("/me/email-sources/test") && init?.method === "POST")
        return jsonResponse(200, { ok: true, message_count: 3, folders: [], capabilities: [], error: null, proxy_unverified: false });
      if (u.includes("/me/email-sources") && init?.method === "POST")
        return jsonResponse(201, { status: "created", source_id: "src-1", source_type: "imap", url: "imap://imap.gmail.com:993/personal", extraction_method: "imap_metadata" });
      if (u.includes("/connector-credentials/imap") && init?.method === "PUT")
        return jsonResponse(200, { connector: "imap", instance: "src-1" });
      if (u.includes("/smtp") && init?.method === "PUT")
        return jsonResponse(200, { ok: true, enabled: true, tested: true });
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    renderForm(fetchImpl);
    const u = await pickGmailAndFill();
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    await u.click(screen.getByLabelText(/let lumogis send as me/i));
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/src-1/smtp") && c.method === "PUT")).toBe(true),
    );
    const smtpCall = calls.find((c) => c.url.endsWith("/src-1/smtp"));
    expect(JSON.parse(String(smtpCall?.body))).toMatchObject({ enabled: true, reuse_imap_password: true });
  });

  // LUM-687 (674b) — OAuth device-flow provider (Outlook). Picking an OAuth
  // provider routes to the device-code panel, never the app-password form.
  describe("OAuth device-flow (Outlook)", () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it("picking Outlook shows the email+label step, not an app-password field", async () => {
      renderForm(makeFetch());
      const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await waitFor(() => expect(screen.getByRole("button", { name: /Outlook/ })).toBeInTheDocument());
      await u.click(screen.getByRole("button", { name: /Outlook/ }));
      expect(screen.getByLabelText(/email address/i)).toBeInTheDocument();
      // No app-password field on the OAuth path.
      expect(screen.queryByLabelText(/app password/i)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /test connection/i })).not.toBeInTheDocument();
    });

    // LUM-691 — import-scope parity: the OAuth branch shows the same picker as
    // the app-password path, validates custom-needs-since before starting, and
    // forwards the chosen scope/dates into the device start body.
    it("shows the import-scope picker on the OAuth path, defaulting to last 30 days", async () => {
      renderForm(makeFetch());
      const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await waitFor(() => expect(screen.getByRole("button", { name: /Outlook/ })).toBeInTheDocument());
      await u.click(screen.getByRole("button", { name: /Outlook/ }));
      expect(screen.getByText(/how much history to import/i)).toBeInTheDocument();
      expect(screen.getByRole("radio", { name: /last 30 days/i })).toBeChecked();
    });

    it("custom scope without a start date blocks Continue with an error", async () => {
      renderForm(makeFetch());
      const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await waitFor(() => expect(screen.getByRole("button", { name: /Outlook/ })).toBeInTheDocument());
      await u.click(screen.getByRole("button", { name: /Outlook/ }));
      await u.type(screen.getByLabelText(/email address/i), "me@outlook.com");
      await u.click(screen.getByLabelText(/custom date range/i));
      await u.click(screen.getByRole("button", { name: /continue to outlook\.com sign-in/i }));
      expect(screen.getByRole("alert")).toHaveTextContent(/custom date range needs a start date/i);
      // Never started the device flow.
      expect(screen.queryByLabelText(/device code/i)).not.toBeInTheDocument();
    });

    it("sends the chosen import scope + dates in the device start body", async () => {
      const calls: { url: string; method?: string; body?: string }[] = [];
      const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
        const u = String(input);
        calls.push({ url: u, method: init?.method, body: init?.body as string | undefined });
        if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
        if (u.includes("/api/v1/me/email-providers")) return jsonResponse(200, PROVIDERS);
        if (u.includes("/oauth/") && u.includes("/device/start") && init?.method === "POST") {
          return jsonResponse(200, {
            flow_id: "F1", user_code: "WXYZ-1234",
            verification_uri: "https://ms.example/dev", verification_uri_complete: null,
            interval: 1, expires_in: 900,
          });
        }
        if (u.includes("/oauth/") && u.includes("/device/poll") && init?.method === "POST")
          return jsonResponse(200, { status: "pending" });
        return jsonResponse(404, {});
      }) as unknown as typeof fetch;
      renderForm(fetchImpl);
      const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await waitFor(() => expect(screen.getByRole("button", { name: /Outlook/ })).toBeInTheDocument());
      await u.click(screen.getByRole("button", { name: /Outlook/ }));
      await u.type(screen.getByLabelText(/email address/i), "me@outlook.com");
      await u.click(screen.getByLabelText(/custom date range/i));
      await u.type(screen.getByLabelText(/import from date/i), "2026-01-01");
      await u.click(screen.getByRole("button", { name: /continue to outlook\.com sign-in/i }));
      await screen.findByLabelText(/device code/i);
      const startCall = calls.find((c) => c.url.includes("/device/start"));
      expect(JSON.parse(String(startCall?.body))).toMatchObject({
        import_scope: "custom", import_since: "2026-01-01",
      });
    });

    it("continues to the device panel and shows the user code", async () => {
      renderForm(makeFetch({ pollStatus: "pending" }));
      const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await waitFor(() => expect(screen.getByRole("button", { name: /Outlook/ })).toBeInTheDocument());
      await u.click(screen.getByRole("button", { name: /Outlook/ }));
      await u.type(screen.getByLabelText(/email address/i), "me@outlook.com");
      await u.click(screen.getByRole("button", { name: /continue to outlook\.com sign-in/i }));
      expect(await screen.findByLabelText(/device code/i)).toHaveTextContent("WXYZ-1234");
    });

    it("connects on a successful poll and calls onCreated", async () => {
      const onCreated = vi.fn();
      const store = new AccessTokenStore();
      const client = new ApiClient({ tokens: store, fetchImpl: makeFetch({ pollStatus: "connected", pollSourceId: "src-x", pollVerified: true }) });
      render(
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <EmailMailboxSetupForm onCreated={onCreated} />
        </AuthProvider>,
      );
      const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await waitFor(() => expect(screen.getByRole("button", { name: /Outlook/ })).toBeInTheDocument());
      await u.click(screen.getByRole("button", { name: /Outlook/ }));
      await u.type(screen.getByLabelText(/email address/i), "me@outlook.com");
      await u.click(screen.getByRole("button", { name: /continue to outlook\.com sign-in/i }));
      await screen.findByLabelText(/device code/i);
      await vi.advanceTimersByTimeAsync(1000); // fire the first poll → connected
      await waitFor(() => expect(onCreated).toHaveBeenCalled());
    });
  });
});

// LUM-758 — tell the member BEFORE they finish whether this will actually sync.
//
// The proxy now applies its own allow file, but two gates can still hold a new
// mailbox back and neither is visible from the setup screen: the in-process egress
// ceiling (bakes its allowlist at boot — LUM-636), and the allow-file watcher being
// switched off. Saying nothing is what produced the original bug: a flow that looks
// finished and a mailbox that silently never syncs.
describe("LUM-758 operator-action notice", () => {
  it("says nothing when the mailbox will sync on its own", async () => {
    renderForm(makeFetch());
    await waitFor(() => expect(screen.getByRole("button", { name: /Gmail/ })).toBeInTheDocument());
    expect(screen.queryByTestId("operator-action-notice")).toBeNull();
  });

  it("warns about a restart when the egress ceiling is on", async () => {
    renderForm(
      makeFetch({
        providers: {
          providers: [GMAIL],
          new_mailbox_needs_operator: true,
          operator_action_reason: "ceiling",
        },
      }),
    );
    const notice = await screen.findByTestId("operator-action-notice");
    expect(notice.textContent).toMatch(/restarts Lumogis/i);
  });

  it("warns when the allow-file watcher has been switched off", async () => {
    renderForm(
      makeFetch({
        providers: {
          providers: [GMAIL],
          new_mailbox_needs_operator: true,
          operator_action_reason: "watch_disabled",
        },
      }),
    );
    const notice = await screen.findByTestId("operator-action-notice");
    expect(notice.textContent).toMatch(/applies the change/i);
  });

  it("stays silent against an older backend that omits the fields", async () => {
    // Optional-safe: absent → falsy → no warning, rather than a scary default.
    renderForm(makeFetch({ providers: { providers: [GMAIL] } }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Gmail/ })).toBeInTheDocument());
    expect(screen.queryByTestId("operator-action-notice")).toBeNull();
  });
});

// LUM-759 — the send opt-in used to fail into an empty `catch {}`: a user who
// ticked "let Lumogis send as me" got a connected mailbox with sending silently
// off and no message. These drive real status codes through the fetch mock, so
// ApiClient → ApiError → the component runs for real rather than being stubbed.
describe("LUM-759 send opt-in failure notice", () => {
  function fetchWithSmtpFailure(status: number, detail: unknown) {
    return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/api/v1/me/email-providers")) return jsonResponse(200, PROVIDERS);
      if (u.includes("/me/email-sources/test") && init?.method === "POST")
        return jsonResponse(200, {
          ok: true, message_count: 3, folders: [], capabilities: [], error: null,
          proxy_unverified: false,
        });
      if (u.includes("/me/email-sources") && init?.method === "POST")
        return jsonResponse(201, {
          status: "created", source_id: "src-1", source_type: "imap",
          url: "imap://imap.gmail.com:993/personal", extraction_method: "imap_metadata",
        });
      if (u.includes("/connector-credentials/imap") && init?.method === "PUT")
        return jsonResponse(200, { connector: "imap", instance: "src-1" });
      if (u.includes("/smtp") && init?.method === "PUT") return jsonResponse(status, { detail });
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
  }

  async function submitWithSendEnabled(fetchImpl: typeof fetch, onCreated?: () => void) {
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <EmailMailboxSetupForm onCreated={onCreated ?? (() => undefined)} />
      </AuthProvider>,
    );
    const u = await pickGmailAndFill();
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    await u.click(screen.getByLabelText(/let lumogis send as me/i));
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));
    return screen.findByTestId("send-optin-notice");
  }

  it("a 503 says try again, not check your settings", async () => {
    const notice = await submitWithSendEnabled(
      fetchWithSmtpFailure(503, { code: "mailbox_unreachable" }),
    );
    expect(notice.textContent).toMatch(/clears up on its own/i);
    expect(notice.textContent).not.toMatch(/settings/i);
    expect(notice.textContent).toMatch(/Mailbox connected/i);
  });

  it("an egress block points at the operator", async () => {
    const notice = await submitWithSendEnabled(
      fetchWithSmtpFailure(503, { code: "egress_blocked" }),
    );
    expect(notice.textContent).toMatch(/ask your Lumogis operator to allow the host/i);
    expect(notice.textContent).toMatch(/credentials are fine/i);
  });

  it("a revoked grant offers reconnect", async () => {
    const notice = await submitWithSendEnabled(
      fetchWithSmtpFailure(422, { code: "imap_reauth_required" }),
    );
    expect(notice.textContent).toMatch(/sign-in has expired/i);
    expect(notice.textContent).toMatch(/reconnect it/i);
  });

  it("the mailbox still connects when the send opt-in fails", async () => {
    // The property the empty catch was protecting, and the regression this
    // whole change risks: reading must not become conditional on sending.
    const onCreated = vi.fn();
    await submitWithSendEnabled(
      fetchWithSmtpFailure(503, { code: "mailbox_unreachable" }),
      onCreated,
    );
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  });

  it("an unknown code still says something useful", async () => {
    const notice = await submitWithSendEnabled(
      fetchWithSmtpFailure(503, { code: "something_new_entirely" }),
    );
    // LUM-769: the fallback string is now a standalone sentence. Every string in
    // `sendOptInMessage.ts` was a lowercase continuation of the prefix's em-dash,
    // which meant the mailbox card — which passes a different framing — rendered
    // half of them starting mid-sentence. The PREFIX is unchanged for this surface.
    expect(notice.textContent).toBe(
      "Mailbox connected. Sending isn't on yet — You can try again later.",
    );
  });

  it("a successful opt-in shows no notice", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/api/v1/me/email-providers")) return jsonResponse(200, PROVIDERS);
      if (u.includes("/me/email-sources/test") && init?.method === "POST")
        return jsonResponse(200, {
          ok: true, message_count: 3, folders: [], capabilities: [], error: null,
          proxy_unverified: false,
        });
      if (u.includes("/me/email-sources") && init?.method === "POST")
        return jsonResponse(201, {
          status: "created", source_id: "src-1", source_type: "imap",
          url: "imap://imap.gmail.com:993/personal", extraction_method: "imap_metadata",
        });
      if (u.includes("/connector-credentials/imap") && init?.method === "PUT")
        return jsonResponse(200, { connector: "imap", instance: "src-1" });
      if (u.includes("/smtp") && init?.method === "PUT")
        return jsonResponse(200, { ok: true, enabled: true, tested: true });
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    const onCreated = vi.fn();
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <EmailMailboxSetupForm onCreated={onCreated} />
      </AuthProvider>,
    );
    const u = await pickGmailAndFill();
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    await u.click(screen.getByLabelText(/let lumogis send as me/i));
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(screen.queryByTestId("send-optin-notice")).toBeNull();
  });

  it.each([
    ["a recognised code", "mailbox_unreachable"],
    ["the fallback branch", "no_such_code"],
  ])("never renders the raw error body — %s", async (_label, code) => {
    // The regression this guards is ONE line of plausible code: an `err.message`
    // fallback. ApiError.message is `HTTP ${status}: ${detail}` and detail is
    // JSON.stringify(body) for an object detail.
    //
    // The unrecognised-code row is the one that matters and it was ADDED AFTER A
    // MUTATION EXPOSED THE GAP: the first version of this test used only a
    // recognised code, so the default branch — the exact place an `err.message`
    // fallback would live — was never exercised. Planting that fallback left this
    // test green. A test whose name promises more than its assertion delivers is
    // the failure mode LUM-757's post-implementation review found twice.
    const notice = await submitWithSendEnabled(
      fetchWithSmtpFailure(503, { code, message: "connect to SEKRIT-host.internal:993 failed" }),
    );
    expect(notice.textContent).not.toMatch(/SEKRIT/);
    expect(document.body.textContent).not.toMatch(/SEKRIT/);
  });

  it.each([
    ["mailbox_unreachable", /clears up on its own/i],
    ["egress_blocked", /allow the host/i],
    ["oauth_unavailable", /sign-in for this provider isn't configured/i],
    ["credential_unavailable", /couldn't read this mailbox's stored credentials/i],
    ["imap_reauth_required", /sign-in has expired/i],
    ["imap_settings_invalid", /stored settings can't be used/i],
    ["imap_not_configured", /no sign-in stored for this mailbox/i],
  ])("every specified notice string is reachable: %s", async (code, expected) => {
    // Without this, four of the eight strings could render the generic fallback
    // with the suite green — specified copy that is never asserted is decorative.
    const notice = await submitWithSendEnabled(fetchWithSmtpFailure(503, { code }));
    expect(notice.textContent).toMatch(expected);
  });

  it("a stale notice does not survive a later successful attempt", async () => {
    // Found by code review at /verify-plan, not by any test: `setError(null)`
    // resets on every submit and `setSendNotice` did not, so a notice from a
    // failed attempt persisted across a later successful one — the user reads
    // "Sending isn't on yet" about a mailbox where sending is now on.
    let smtpShouldFail = true;
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/api/v1/me/email-providers")) return jsonResponse(200, PROVIDERS);
      if (u.includes("/me/email-sources/test") && init?.method === "POST")
        return jsonResponse(200, {
          ok: true, message_count: 3, folders: [], capabilities: [], error: null,
          proxy_unverified: false,
        });
      if (u.includes("/me/email-sources") && init?.method === "POST")
        return jsonResponse(201, {
          status: "created", source_id: "src-1", source_type: "imap",
          url: "imap://imap.gmail.com:993/personal", extraction_method: "imap_metadata",
        });
      if (u.includes("/connector-credentials/imap") && init?.method === "PUT")
        return jsonResponse(200, { connector: "imap", instance: "src-1" });
      if (u.includes("/smtp") && init?.method === "PUT") {
        if (smtpShouldFail) return jsonResponse(503, { detail: { code: "mailbox_unreachable" } });
        return jsonResponse(200, { ok: true, enabled: true, tested: true });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;

    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <EmailMailboxSetupForm onCreated={() => undefined} />
      </AuthProvider>,
    );
    const u = await pickGmailAndFill();
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    await u.click(screen.getByLabelText(/let lumogis send as me/i));
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));
    await screen.findByTestId("send-optin-notice");

    smtpShouldFail = false;
    // The form clears the password on success, and the field is `required`, so a
    // second submit is blocked by native validation until it is re-entered. That
    // is also the only realistic route back here: the user re-types and retries.
    await u.type(screen.getByLabelText(/app password/i), "app-pw-2");
    // Editing any credential field calls `invalidateTest()`, which clears the
    // probe and re-disables Save — so the retry path necessarily runs the
    // connection test again. This is the real sequence, not a shortcut.
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /add mailbox/i })).toBeEnabled(),
    );
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));
    await waitFor(() => expect(screen.queryByTestId("send-optin-notice")).toBeNull());
  });

  it("a network-level rejection still shows the notice", async () => {
    // Every other case feeds the fallback an ApiError. This exercises the
    // `unknown`-typed branch that actually catches a dropped connection.
    const onCreated = vi.fn();
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/api/v1/me/email-providers")) return jsonResponse(200, PROVIDERS);
      if (u.includes("/me/email-sources/test") && init?.method === "POST")
        return jsonResponse(200, {
          ok: true, message_count: 3, folders: [], capabilities: [], error: null,
          proxy_unverified: false,
        });
      if (u.includes("/me/email-sources") && init?.method === "POST")
        return jsonResponse(201, {
          status: "created", source_id: "src-1", source_type: "imap",
          url: "imap://imap.gmail.com:993/personal", extraction_method: "imap_metadata",
        });
      if (u.includes("/connector-credentials/imap") && init?.method === "PUT")
        return jsonResponse(200, { connector: "imap", instance: "src-1" });
      if (u.includes("/smtp") && init?.method === "PUT") throw new TypeError("Failed to fetch");
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <EmailMailboxSetupForm onCreated={onCreated} />
      </AuthProvider>,
    );
    const u = await pickGmailAndFill();
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    await u.click(screen.getByLabelText(/let lumogis send as me/i));
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));
    const notice = await screen.findByTestId("send-optin-notice");
    // LUM-769: the fallback string is now a standalone sentence. Every string in
    // `sendOptInMessage.ts` was a lowercase continuation of the prefix's em-dash,
    // which meant the mailbox card — which passes a different framing — rendered
    // half of them starting mid-sentence. The PREFIX is unchanged for this surface.
    expect(notice.textContent).toBe(
      "Mailbox connected. Sending isn't on yet — You can try again later.",
    );
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
  });
});

describe("LUM-775 the sending fields are actually required", () => {
  // The three SMTP inputs behind "let Lumogis send as me" carried no `required`
  // while every IMAP field beside them did, and the submit handler sends
  // `sendSmtpHost.trim() || undefined` — so a blank OR whitespace-only host
  // reached the server as a MISSING host and came back `422 smtp_host_required`,
  // rendered (until LUM-769) as the generic "you can try again later".
  //
  // These assert the mechanism FIRES, not that the attribute is in the DOM.
  // That distinction is the whole ticket: on the mailbox card the same `required`
  // was inert because there was no <form> to gate the submit. Here there is one.
  function fetchOk() {
    return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/api/v1/me/email-providers")) return jsonResponse(200, PROVIDERS);
      if (u.includes("/me/email-sources/test") && init?.method === "POST")
        return jsonResponse(200, {
          ok: true, message_count: 3, folders: [], capabilities: [], error: null,
          proxy_unverified: false,
        });
      if (u.includes("/me/email-sources") && init?.method === "POST")
        return jsonResponse(201, {
          status: "created", source_id: "src-1", source_type: "imap",
          url: "imap://mail.selfhosted.example:993/personal",
          extraction_method: "imap_metadata",
        });
      if (u.includes("/connector-credentials/imap") && init?.method === "PUT")
        return jsonResponse(200, { connector: "imap", instance: "src-1" });
      if (u.includes("/smtp") && init?.method === "PUT")
        return jsonResponse(200, { ok: true, enabled: true, tested: true, error: null });
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
  }

  async function fillOtherWithSend(fetchImpl: typeof fetch) {
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <EmailMailboxSetupForm onCreated={() => undefined} />
      </AuthProvider>,
    );
    const u = userEvent.setup();
    // "Other" is the manual-entry card — the only path that shows SMTP host/port.
    await waitFor(() => expect(screen.getByRole("button", { name: /^Other/ })).toBeInTheDocument());
    await u.click(screen.getByRole("button", { name: /^Other/ }));
    await u.type(screen.getByLabelText(/imap host/i), "mail.selfhosted.example");
    await u.type(screen.getByLabelText(/^username/i), "me@selfhosted.example");
    await u.type(screen.getByLabelText(/app password/i), "app-pw");
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    await u.click(screen.getByLabelText(/let lumogis send as me/i));
    return u;
  }

  it("blocks the submit when the sending host is only whitespace", async () => {
    const fetchImpl = fetchOk();
    const u = await fillOtherWithSend(fetchImpl);

    await u.type(screen.getByLabelText(/smtp host/i), "   ");
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));

    // No mailbox was created at all — native validation stopped the form before
    // any request. `required` alone would NOT catch this: the value is
    // non-empty until it is trimmed, which is why the field trims on entry.
    const posts = (fetchImpl as unknown as Mock).mock.calls.filter(
      (c) => String(c[0]).includes("/me/email-sources") && !String(c[0]).includes("/test"),
    );
    expect(posts).toHaveLength(0);
    expect(screen.getByLabelText(/smtp host/i)).toBeInvalid();
  });

  it("blocks the submit when a separate sending password is left blank", async () => {
    const fetchImpl = fetchOk();
    const u = await fillOtherWithSend(fetchImpl);

    await u.type(screen.getByLabelText(/smtp host/i), "smtp.selfhosted.example");
    await u.click(screen.getByLabelText(/use my mailbox password/i)); // untick → field appears
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));

    const posts = (fetchImpl as unknown as Mock).mock.calls.filter(
      (c) => String(c[0]).includes("/me/email-sources") && !String(c[0]).includes("/test"),
    );
    expect(posts).toHaveLength(0);
    expect(screen.getByLabelText(/smtp app password/i)).toBeInvalid();
  });

  it("submits once the sending fields are filled", async () => {
    const fetchImpl = fetchOk();
    const u = await fillOtherWithSend(fetchImpl);

    await u.type(screen.getByLabelText(/smtp host/i), "smtp.selfhosted.example");
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));

    await waitFor(() => {
      const posts = (fetchImpl as unknown as Mock).mock.calls.filter(
        (c) => String(c[0]).includes("/me/email-sources") && !String(c[0]).includes("/test"),
      );
      expect(posts.length).toBeGreaterThan(0);
    });
  });
});

describe("LUM-770 no server-supplied string reaches the DOM", () => {
  // The submit handler's outer catch rendered `String(err.detail)` — the
  // serialised response body — and, on any route that passes an exception
  // message through, a credential-layer string naming a host, a payload field
  // or a user_id. ADR-197 established the opposite rule for the SIBLING catch
  // three lines away; this one predated it.
  //
  // The sentinel is parametrised over a recognised code AND the fallback,
  // because the fallback is where a `String(err.detail)` regression would live —
  // LUM-759 shipped a sentinel guarding only the recognised path and a mutation
  // left it green.
  function fetchFailingSave(detail: unknown, status = 422) {
    return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/api/v1/me/email-providers")) return jsonResponse(200, PROVIDERS);
      if (u.includes("/me/email-sources/test") && init?.method === "POST")
        return jsonResponse(200, {
          ok: true, message_count: 3, folders: [], capabilities: [], error: null,
          proxy_unverified: false,
        });
      if (u.includes("/me/email-sources") && init?.method === "POST")
        return jsonResponse(status, { detail });
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
  }

  async function saveAndReadError(fetchImpl: typeof fetch) {
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <EmailMailboxSetupForm onCreated={() => undefined} />
      </AuthProvider>,
    );
    const u = await pickGmailAndFill();
    await u.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    await u.click(screen.getByRole("button", { name: /add mailbox/i }));
    return waitFor(() => {
      const el = screen.getByRole("alert");
      expect(el).toBeInTheDocument();
      return el;
    });
  }

  it("renders a human sentence for a recognised code, not the envelope", async () => {
    await saveAndReadError(fetchFailingSave({ code: "bad_port", error: "bad_port" }));
    expect(document.body.textContent).toContain("port");
    // The shape the old code produced.
    expect(document.body.textContent).not.toContain('{"');
    expect(document.body.textContent).not.toContain("detail");
  });

  it("never leaks the body on the fallback path either", async () => {
    // An unrecognised code — where a `String(err.detail)` regression would live.
    await saveAndReadError(
      fetchFailingSave({ code: "a_code_this_client_has_never_seen", host: "SEKRIT-host.internal" }),
    );
    expect(document.body.textContent).not.toContain("SEKRIT");
    expect(document.body.textContent).not.toContain('{"');
    expect(document.body.textContent).toContain("Couldn't add the mailbox");
  });

  it("never leaks a bare exception message passed through as detail", async () => {
    // Some routes pass an exception string straight into `detail`. Credential-layer
    // messages name hosts, payload fields and user_ids.
    await saveAndReadError(fetchFailingSave("decrypt failed for user_id=default host=SEKRIT.internal", 503));
    expect(document.body.textContent).not.toContain("SEKRIT");
    expect(document.body.textContent).not.toContain("user_id");
  });
});
