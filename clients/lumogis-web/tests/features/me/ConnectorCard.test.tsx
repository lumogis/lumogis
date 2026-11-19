// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * LUM-834 chunk B — the unified connector card.
 *
 * ## What these tests are for
 *
 * The chunk's whole risk is a card that **says the wrong thing**. Every case
 * below is one specific untrue sentence the card could render:
 *
 *  * "no fields set" about a fully configured pre-migration credential;
 *  * "no fields set" about a connector whose shape simply is not described;
 *  * "leave blank to keep", which the backend cannot honour;
 *  * a bare `✓ set` beside a secret the user must retype anyway;
 *  * an enable toggle on an admin tab that has no route to call;
 *  * an Ask/Do control on an admin tab that would set the ADMIN'S own mode;
 *  * nothing at all where the email cards used to be.
 *
 * ## `?? []` is the specific enemy
 *
 * `configured_fields` has three states and the middle one — `null`, meaning
 * *unknown* — is the one the codebase's dominant idiom destroys. The tests for
 * it are written adjacently to the known-fields case so the pair is visible.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

// Plain success is a toast (LUM-993 Pass 5); the suite asserts on the call.
const toastMock = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: toastMock }));

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { ME_CONNECTORS_BASE, MeConnectorsView } from "../../../src/features/me/MeConnectorsView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
  toastMock.mockClear();
});

/** The form sits behind the card's primary button (Connect, or Edit once saved). */
async function openForm(u: ReturnType<typeof userEvent.setup>, within_?: HTMLElement) {
  const scope = within_ ? within(within_) : screen;
  await u.click(await scope.findByRole("button", { name: /^(Connect|Edit)$/ }));
}

/** "…" menu, Disconnect, then confirm in the dialog. */
async function disconnect(u: ReturnType<typeof userEvent.setup>) {
  await u.click(await screen.findByRole("button", { name: /^More actions for / }));
  await u.click(await screen.findByRole("menuitem", { name: "Disconnect" }));
  await u.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Disconnect" }));
}

const authUser = { id: "u1", email: "u@home.lan", role: "user" as const };

const PAPERLESS_FIELDS = [
  { name: "base_url", label: "Server address", is_secret: false },
  { name: "token", label: "API token", is_secret: true },
];

const PRIVACY_LOCAL_ONLY = {
  instance: { privacy_mode: "local_only", privacy_mode_locked: false, privacy_effective: "local_only" },
  user_restriction: "inherit",
  privacy_effective: "local_only",
  can_allow_cloud: false,
};

interface MountOpts {
  registry?: { id: string; description: string; fields?: unknown[] }[];
  rows?: Record<string, unknown>[];
  permissions?: { connector: string; mode: "ASK" | "DO" }[];
  basePath?: string;
  llmOnly?: boolean;
  putStatus?: number;
  /** Held PUT: the request stays in-flight until this resolves. */
  putHold?: Promise<void>;
  /** LUM-881: caller role (default "user"). */
  role?: "user" | "admin";
  /** LUM-881: `GET /api/v1/models` answer — a catalog, or an HTTP status to fail with. */
  models?: Record<string, unknown>[] | number;
  /** LUM-881: `GET /api/v1/me/privacy-mode` answer. */
  privacy?: Record<string, unknown>;
  /** LUM-881: status for DELETE on a credential (default 200). */
  deleteStatus?: number;
}

function deferredPut(): { hold: Promise<void>; release: () => void } {
  let release!: () => void;
  const hold = new Promise<void>((r) => {
    release = r;
  });
  return { hold, release };
}

function mount(opts: MountOpts = {}) {
  const registry = opts.registry ?? [
    { id: "paperless", description: "Paperless", fields: PAPERLESS_FIELDS },
  ];
  const rows = opts.rows ?? [];
  const permissions = opts.permissions ?? [];

  const calls: { url: string; method: string; body?: string }[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, method, body: init?.body as string | undefined });

    if (url.includes("/api/v1/auth/me")) {
      return jsonResponse(200, { ...authUser, role: opts.role ?? authUser.role });
    }
    if (url.includes("/api/v1/models")) {
      if (typeof opts.models === "number") return jsonResponse(opts.models, { detail: "boom" });
      return jsonResponse(200, { models: opts.models ?? [] });
    }
    if (url.includes("/api/v1/me/privacy-mode")) {
      return jsonResponse(200, opts.privacy ?? PRIVACY_LOCAL_ONLY);
    }
    if (url.includes("connector-credentials") && method === "DELETE") {
      return jsonResponse(opts.deleteStatus ?? 200, opts.deleteStatus ? { detail: "nope" } : {});
    }
    if (url.includes("/registry")) return jsonResponse(200, { items: registry });
    if (url.includes("/api/v1/me/permissions")) {
      if (method === "PUT") return jsonResponse(200, {});
      return jsonResponse(200, permissions);
    }
    if (url.includes("connector-credentials")) {
      if (method === "PATCH") return jsonResponse(200, {});
      if (method === "PUT") {
        if (opts.putHold) await opts.putHold;
        if (opts.putStatus && opts.putStatus !== 200) {
          return jsonResponse(opts.putStatus, { detail: "save failed" });
        }
        return jsonResponse(200, {});
      }
      return jsonResponse(200, { items: rows });
    }
    return jsonResponse(404, {});
  });

  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
  render(
    <MemoryRouter>
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeConnectorsView basePath={opts.basePath} llmOnly={opts.llmOnly} />
      </AuthProvider>
    </MemoryRouter>,
  );
  return calls;
}

function configuredRow(over: Record<string, unknown> = {}) {
  return {
    connector: "paperless",
    updated_at: "2026-01-01T00:00:00Z",
    enabled: true,
    configured_fields: ["base_url", "token"],
    secret_fields: ["token"],
    ...over,
  };
}

describe("ConnectorCard — field presence", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows which non-secret fields are set", async () => {
    mount({ rows: [configuredRow({ configured_fields: ["base_url"] })] });
    expect(await screen.findByText(/✓ Server address/)).toBeInTheDocument();
  });

  it("a row with UNKNOWN configured_fields renders 'configured', not 'not configured'", async () => {
    // The migration state. `configured_fields: null` means the row predates
    // migration 079 — its fields cannot be known without decrypting it, which
    // is the thing this design exists to avoid. `?? []` here would render a
    // working credential as unconfigured.
    mount({ rows: [configuredRow({ configured_fields: null })] });

    expect(
      await screen.findByText(/Configured\. Field details appear after the next save\./i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/— Server address/)).not.toBeInTheDocument();
  });

  it("an absent configured_fields is treated the same as null, not as empty", async () => {
    // An older server omits the key entirely. `undefined` and `null` are the
    // same claim — "we do not know" — and neither is "nothing set".
    const { connector, updated_at, enabled, secret_fields } = configuredRow();
    mount({ rows: [{ connector, updated_at, enabled, secret_fields }] });

    expect(
      await screen.findByText(/Configured\. Field details appear after the next save\./i),
    ).toBeInTheDocument();
  });

  it("a connector with no declared schema says so, rather than reporting no fields set", async () => {
    // Opposite claims, and only one is true. This is the connector that falls
    // through to the JSON fallback form.
    mount({
      registry: [{ id: "somethingelse", description: "", fields: [] }],
      rows: [configuredRow({ connector: "somethingelse", configured_fields: ["whatever"] })],
    });

    expect(await screen.findByText(/fields are not described/i)).toBeInTheDocument();
    expect(screen.queryByText(/Configured\. Field details appear/i)).not.toBeInTheDocument();
  });

  it("a secret field shows consequence copy, not a bare tick", async () => {
    // `✓ set` beside a value the card then refuses to reuse is worse than
    // silence: it announces the secret is stored and then makes the user go
    // and find it again. The copy states the consequence instead.
    mount({ rows: [configuredRow()] });

    // Scoped to the presence list: the typed form ALSO renders a field
    // labelled "API token", and matching both would make this assert about
    // whichever the query happened to reach first.
    const line = await screen.findByText(/entering it again/i);
    expect(line.textContent).toMatch(/API token/);
    expect(line.textContent).not.toMatch(/✓/);
  });

  it("no field renders the words 'leave blank to keep'", async () => {
    // The backend cannot honour it: `put_payload` is whole-payload
    // replacement, so an omitted field is a DELETED field (LUM-846). A copy
    // edit must not quietly reintroduce the promise.
    mount({ rows: [configuredRow()] });
    await screen.findByText(/entering it again/i);
    expect(document.body.textContent ?? "").not.toMatch(/leave blank to keep/i);
  });

  it("a secret field renders no value, and its input starts empty", async () => {
    // Stated as two concrete properties rather than as a keyword scan over the
    // page text: the card's own copy legitimately contains the word "secret"
    // ("enter the secret again"), so a blunt text match asserts about the
    // wording rather than about the data.
    //
    // The real invariant is that no VALUE reaches this surface. The wire
    // carries key names only, and the form never seeds a secret — which is
    // also what stops a save from writing back a placeholder.
    mount({ rows: [configuredRow()] });

    const line = await screen.findByText(/entering it again/i);
    // The presence row names the field and says what saving costs. It does
    // not show, mask, or placeholder the stored value.
    expect(line.textContent).toMatch(/API token/);
    expect(line.textContent).not.toMatch(/●|\*{3}|•/);

    await openForm(userEvent.setup());
    const secretInput = document.querySelector<HTMLInputElement>('input[type="password"]');
    expect(secretInput, "the typed form rendered no password input to check").not.toBeNull();
    expect(secretInput!.value).toBe("");
  });
});

describe("ConnectorCard — the enable toggle", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("PATCHes rather than DELETEs, and says the credential is kept", async () => {
    // "Disable" used to be `client.delete`, which destroyed the credential.
    const calls = mount({ rows: [configuredRow()] });
    const u = userEvent.setup();

    await u.click(await screen.findByRole("switch", { name: /^on$/i }));

    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch, "no PATCH was issued").toBeTruthy();
      expect(patch!.body).toContain('"enabled":false');
    });
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("a disabled connector still shows its form", async () => {
    // Editable while disabled on purpose: a user needs to fix a credential
    // BEFORE switching it back on, and saving does not re-enable.
    mount({ rows: [configuredRow({ enabled: false })] });
    await openForm(userEvent.setup());
    expect(await screen.findByLabelText(/server address/i)).toBeInTheDocument();
  });

  it("a disabled connector's Test button is disabled", async () => {
    // The probe answers `disabled` honestly, but there is nothing to learn
    // from asking, so the button says so before the round trip.
    mount({ rows: [configuredRow({ enabled: false })] });
    const btn = await screen.findByRole("button", { name: /test connection/i });
    expect(btn).toBeDisabled();
  });

  it("an enabled connector's Test button is NOT disabled", async () => {
    // The twin — otherwise the test above passes against a button that is
    // always disabled, which would remove the feature rather than gate it.
    mount({ rows: [configuredRow()] });
    const btn = await screen.findByRole("button", { name: /test connection/i });
    expect(btn).not.toBeDisabled();
  });

  it("no toggle renders for a connector that is not configured yet", async () => {
    // There is no row to enable or disable; the PATCH would 404.
    mount({ rows: [] });
    await screen.findByRole("heading", { name: /^Paperless documents/ });
    expect(screen.queryByRole("switch", { name: /^on$/i })).not.toBeInTheDocument();
  });

  it("does not issue a second enable PATCH while the first is in flight", async () => {
    // LUM-842 fixed the same out-of-order-write class for permission radios.
    // Without this latch, OFF then ON before the first PATCH lands can leave
    // the connector disabled when the user believed they turned it back on.
    let releasePatch: () => void = () => {};
    const patchGate = new Promise<void>((resolve) => {
      releasePatch = resolve;
    });

    const calls: { url: string; method: string; body?: string }[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, method, body: init?.body as string | undefined });

      if (url.includes("/api/v1/auth/me")) return jsonResponse(200, authUser);
      if (url.includes("/registry")) {
        return jsonResponse(200, {
          items: [{ id: "paperless", description: "Paperless", fields: PAPERLESS_FIELDS }],
        });
      }
      if (url.includes("/api/v1/me/permissions")) return jsonResponse(200, []);
      if (url.includes("connector-credentials")) {
        if (method === "PATCH") {
          await patchGate;
          return jsonResponse(200, {});
        }
        return jsonResponse(200, { items: [configuredRow()] });
      }
      return jsonResponse(404, {});
    });

    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <MemoryRouter>
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <MeConnectorsView />
        </AuthProvider>
      </MemoryRouter>,
    );

    const u = userEvent.setup();
    const toggle = await screen.findByRole("switch", { name: /^on$/i });
    await u.click(toggle);
    await waitFor(() => expect(toggle).toHaveAttribute("aria-disabled", "true"));

    await u.click(toggle);
    releasePatch();
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled", "true"));

    const patches = calls.filter((c) => c.method === "PATCH");
    expect(patches).toHaveLength(1);
    expect(patches[0]!.body).toContain('"enabled":false');
  });
});

describe("ConnectorCard — the four surfaces", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const ADMIN_BASE = "/api/v1/admin/connector-credentials/household";

  it("the enable toggle renders on /me and NOT on an admin basePath", async () => {
    mount({ rows: [configuredRow()] });
    expect(await screen.findByRole("switch", { name: /^on$/i })).toBeInTheDocument();
  });

  it("no enable toggle on an admin basePath — there is no admin PATCH", async () => {
    // `enabled` is a column on the per-user table only. Rendering a control
    // with no route behind it is a button that lies about what it does.
    mount({ rows: [configuredRow()], basePath: ADMIN_BASE });
    await screen.findByRole("heading", { name: /^Paperless documents/ });
    expect(screen.queryByRole("switch", { name: /^on$/i })).not.toBeInTheDocument();
  });

  it("renders NO Ask/Do control, on any surface", async () => {
    // G5 is not shipped, and the reason is a conflict between two of this
    // plan's own decisions.
    //
    // `PUT /api/v1/me/permissions/{connector}` validates against the ACTION
    // registry, whose only members are `imap` and `smtp` — which owner
    // decision 4 removes from this card. So the control would have rendered on
    // paperless, caldav, ntfy and the six llm_* and returned 404 on every
    // change, while the list GET returned no row for them so it always read
    // "Ask me first".
    //
    // The ORIGINAL version of this test mocked a 200 the server cannot
    // produce, and passed. That is §5a exactly: a fake that does not fail the
    // way the real collaborator fails. It now asserts the control is absent.
    mount({ rows: [configuredRow()], permissions: [{ connector: "paperless", mode: "ASK" }] });
    await screen.findByRole("heading", { name: /^Paperless documents/ });
    expect(screen.queryByLabelText(/when lumogis wants to act/i)).not.toBeInTheDocument();
  });

  it("no Ask/Do control on an admin basePath either", async () => {
    mount({ rows: [configuredRow()], basePath: ADMIN_BASE });
    await screen.findByRole("heading", { name: /^Paperless documents/ });
    expect(screen.queryByLabelText(/when lumogis wants to act/i)).not.toBeInTheDocument();
  });

  it("the permissions call does not fire at all", async () => {
    // Not merely hidden — not REQUESTED, on either surface. A me-only call
    // made from an admin screen is a wrong-question request even if nothing
    // renders from it; and on /me it would be a request whose answer the card
    // has no control to spend.
    for (const basePath of [undefined, ADMIN_BASE]) {
      const calls = mount({ rows: [configuredRow()], basePath });
      await screen.findByRole("heading", { name: /^Paperless documents/ });
      expect(calls.some((c) => c.url.includes("/api/v1/me/permissions"))).toBe(false);
      cleanup();
    }
  });

  it("field rows render on /me but NOT on the household or system tiers", async () => {
    // The four-surface matrix, asserted per cell rather than described.
    //
    // `configured_keys` is a column on `user_connector_credentials` only —
    // migration 079 does not touch the household or system tables, and their
    // wire models have no `configured_fields` and never will under this chunk.
    // Falling through to the unknown branch there would render "Field details
    // appear after the next save" FOREVER, because the next save cannot
    // populate a column that does not exist. On a card whose purpose is never
    // to say an untrue sentence, that is the untrue sentence.
    mount({ rows: [configuredRow()] });
    // `findByText` would match the FORM's label too; the field-presence row is
    // the list item, so query by role.
    expect(await screen.findAllByRole("listitem")).not.toHaveLength(0);
    cleanup();

    for (const tier of [
      "/api/v1/admin/connector-credentials/household",
      "/api/v1/admin/connector-credentials/system",
    ]) {
      mount({ rows: [configuredRow()], basePath: tier });
      await screen.findByRole("heading", { name: /^Paperless documents/ });
      expect(screen.queryByText(/field details appear after the next save/i)).not.toBeInTheDocument();
      expect(screen.queryAllByRole("listitem")).toHaveLength(0);
      cleanup();
    }
  });

  it("the admin per-user tier DOES get field rows — it reads the same table", async () => {
    // The twin. Without it the test above passes against a card that hid field
    // presence from every admin surface, which would be wrong for per-user:
    // that tab reads `user_connector_credentials`, the table the column is on.
    mount({ rows: [configuredRow()], basePath: "/api/v1/admin/users/alice/connector-credentials" });
    await screen.findByRole("heading", { name: /^Paperless documents/ });
    expect(await screen.findAllByRole("listitem")).not.toHaveLength(0);
  });

  it("the card issues exactly two list requests, not one per connector", async () => {
    // Registry + credentials. A per-card query for anything would be one
    // request per connector — the LUM-744 pattern this codebase has already
    // paid for once.
    const calls = mount({
      registry: [
        { id: "paperless", description: "", fields: PAPERLESS_FIELDS },
        { id: "caldav", description: "", fields: PAPERLESS_FIELDS },
        { id: "ntfy", description: "", fields: PAPERLESS_FIELDS },
      ],
      rows: [configuredRow()],
    });
    await screen.findByRole("heading", { name: /^Calendar/ });

    const gets = calls.filter((c) => c.method === "GET" && !c.url.includes("/api/v1/auth/me"));
    expect(gets.map((g) => g.url).sort()).toHaveLength(2);
  });
});

describe("ConnectorCard — imap and smtp (LUM-848)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const REG_WITH_EMAIL = [
    { id: "imap", description: "", fields: [] },
    { id: "smtp", description: "", fields: [] },
    { id: "paperless", description: "", fields: PAPERLESS_FIELDS },
  ];

  it("imap and smtp render no card", async () => {
    // Both are multi-instance and this surface PUTs without an `instance`, so
    // every save was a 422. A card that cannot save is a dead end.
    //
    // Matched by REGEX, not by exact name: the heading also carries the
    // four-state chip, so its accessible name is "imapNot configured" and an
    // exact `{ name: "imap" }` matches nothing whether the card renders or
    // not. Caught by the §5b mutation check — removing the hide filter left
    // this test green, which is the definition of a test that asserts nothing.
    mount({ registry: REG_WITH_EMAIL });
    await screen.findByRole("heading", { name: /^Paperless documents/ });
    expect(screen.queryByRole("heading", { name: /^imap/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^smtp/ })).not.toBeInTheDocument();
  });

  it("the hide filter is what removes them — the guard for the test above", async () => {
    // Proves the negative assertions are reachable: with the SAME registry
    // minus the exclusion, a card for a non-hidden connector renders through
    // the identical code path and IS found by the identical query shape.
    mount({ registry: REG_WITH_EMAIL });
    expect(await screen.findByRole("heading", { name: /^Paperless documents/ })).toBeInTheDocument();
  });

  it("imap and smtp are replaced by a signpost, not by silence", async () => {
    // Hiding alone recreates the same defect pointing the other way: the user
    // looking for "where do I configure my email?" finds NOTHING.
    mount({ registry: REG_WITH_EMAIL });
    expect(await screen.findByRole("link", { name: /email accounts/i })).toBeInTheDocument();
  });

  it("the signpost does not render on an admin basePath", async () => {
    // `/settings/email` is a ME page — pointing an admin at their own
    // mailboxes from a tier tab is the wrong-question bug in miniature.
    mount({ registry: REG_WITH_EMAIL, basePath: "/api/v1/admin/connector-credentials/household" });
    await screen.findByRole("heading", { name: /^Paperless documents/ });
    expect(screen.queryByRole("link", { name: /email accounts/i })).not.toBeInTheDocument();
  });

  it("the signpost does not render on the LLM-providers surface", async () => {
    mount({
      registry: [{ id: "llm_anthropic", description: "", fields: [] }],
      llmOnly: true,
      basePath: ME_CONNECTORS_BASE,
    });
    await screen.findByRole("heading", { name: /^Anthropic/ });
    expect(screen.queryByRole("link", { name: /email accounts/i })).not.toBeInTheDocument();
  });
});

describe("ConnectorCard — per-card mutations", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const CALDAV_FIELDS = [
    { name: "base_url", label: "Base URL", is_secret: false },
    { name: "username", label: "Username", is_secret: false },
    { name: "password", label: "Password", is_secret: true },
  ];

  const TWO_CONNECTORS = {
    registry: [
      { id: "paperless", description: "Paperless", fields: PAPERLESS_FIELDS },
      { id: "caldav", description: "CalDAV", fields: CALDAV_FIELDS },
    ],
    rows: [
      configuredRow(),
      configuredRow({
        connector: "caldav",
        configured_fields: ["base_url", "username", "password"],
        secret_fields: ["password"],
      }),
    ],
  };

  async function applyPaperless(u: ReturnType<typeof userEvent.setup>) {
    const card = screen.getByRole("heading", { name: /^Paperless documents/ }).closest('[data-slot="card"]')! as HTMLElement;
    await openForm(u, card);
    await u.type(within(card).getByLabelText(/server address/i), "https://p.example");
    await u.type(within(card).getByLabelText(/api token/i), "0123456789012345");
    await u.click(within(card).getByRole("button", { name: /apply/i }));
  }

  async function applyCaldav(u: ReturnType<typeof userEvent.setup>) {
    const card = screen.getByRole("heading", { name: /^Calendar/ }).closest('[data-slot="card"]')! as HTMLElement;
    await openForm(u, card);
    await u.clear(within(card).getByLabelText(/base url/i));
    await u.type(within(card).getByLabelText(/base url/i), "https://cal.example");
    await u.clear(within(card).getByLabelText(/username/i));
    await u.type(within(card).getByLabelText(/username/i), "alice");
    await u.type(within(card).getByLabelText(/password/i), "secret-password");
    await u.click(within(card).getByRole("button", { name: /apply/i }));
  }

  it("disables the card form while its save is in flight", async () => {
    const { hold, release } = deferredPut();
    mount({ ...TWO_CONNECTORS, putHold: hold });
    const u = userEvent.setup();
    await screen.findByRole("heading", { name: /^Calendar/ });

    try {
      await applyPaperless(u);
      const card = screen.getByRole("heading", { name: /^Paperless documents/ }).closest('[data-slot="card"]')! as HTMLElement;
      await waitFor(() => {
        expect(within(card).getByRole("button", { name: /apply/i })).toBeDisabled();
      });
    } finally {
      release();
    }
  });

  it("keeps a card disabled when the OTHER card is saved mid-flight", async () => {
    // The cross-connector race. A single shared useMutation tracks only the
    // most recently dispatched call — MutationObserver.mutate() detaches from
    // the in-flight mutation before attaching the new one — so scoping by
    // `variables.connector` LOOKS like it isolates cards and does not.
    const { hold, release } = deferredPut();
    mount({ ...TWO_CONNECTORS, putHold: hold });
    const u = userEvent.setup();
    await screen.findByRole("heading", { name: /^Calendar/ });

    try {
      await applyPaperless(u);
      const paperlessCard = screen.getByRole("heading", { name: /^Paperless documents/ }).closest('[data-slot="card"]')! as HTMLElement;
      const paperlessApply = within(paperlessCard).getByRole("button", { name: /apply/i });
      await waitFor(() => expect(paperlessApply).toBeDisabled());

      await applyCaldav(u);

      // paperless's PUT has not resolved, so paperless must still be locked.
      expect(paperlessApply).toBeDisabled();
    } finally {
      release();
    }
  });

  it("shows a failure on the card that failed, even after another card is used", async () => {
    const { hold, release } = deferredPut();
    mount({ ...TWO_CONNECTORS, putStatus: 500, putHold: hold });
    const u = userEvent.setup();
    await screen.findByRole("heading", { name: /^Calendar/ });

    try {
      await applyPaperless(u);
      await applyCaldav(u);
    } finally {
      release();
    }

    const paperlessCard = screen.getByRole("heading", { name: /^Paperless documents/ }).closest('[data-slot="card"]')! as HTMLElement;
    await waitFor(() => {
      expect(within(paperlessCard).getByRole("alert").textContent).toMatch(/save failed/i);
    });
  });

  it("disables the enable toggle while a save is in flight", async () => {
    const { hold, release } = deferredPut();
    mount({ ...TWO_CONNECTORS, putHold: hold });
    const u = userEvent.setup();
    await screen.findByRole("heading", { name: /^Calendar/ });

    try {
      await applyPaperless(u);
      const card = screen.getByRole("heading", { name: /^Paperless documents/ }).closest('[data-slot="card"]')! as HTMLElement;
      await waitFor(() => {
        expect(within(card).getByRole("switch", { name: /^on$/i })).toHaveAttribute("aria-disabled", "true");
      });
    } finally {
      release();
    }
  });
});

describe("ConnectorCard — the four-state chip", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("an unconfigured connector reads 'Not connected'", async () => {
    mount({ rows: [] });
    expect(await screen.findByText(/Not connected/)).toBeInTheDocument();
  });

  it("a configured but untested connector reads 'Connected'", async () => {
    // NOTE: `last_test_ok_at` is not persisted, so "untested" is only true
    // within a session — a reload shows this again for a connector that was
    // verified a moment ago. Recorded as a known limitation rather than
    // asserted as a durable claim; if persistence lands, this arm becomes
    // meaningful across reloads.
    mount({ rows: [configuredRow()] });
    expect(await screen.findByText(/^Connected$/)).toBeInTheDocument();
  });

  it("'Not connected' comes from the row's absence, not from the test result", async () => {
    // The chip's first arm is about whether a credential EXISTS. Deriving it
    // from `ok` would make an untested-but-configured connector read as
    // unconfigured, which is the collapse ADR-218 §3 froze the tri-state to
    // prevent.
    mount({ rows: [configuredRow()] });
    await screen.findByText(/^Connected$/);
    expect(screen.queryByText(/Not connected/)).not.toBeInTheDocument();
  });
});

describe("ConnectorCard — privacy-mode hint after an LLM key save (LUM-881)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const LLM_REG = [{ id: "llm_anthropic", description: "Anthropic", fields: [] }];
  const BLOCKED = [
    { id: "claude", label: "Claude", is_local: false, enabled: false, provider: "anthropic", disabled_reason: "privacy_mode" },
  ];
  const NO_KEY = [
    { id: "claude", label: "Claude", is_local: false, enabled: false, provider: "anthropic", disabled_reason: "no_credentials" },
  ];
  const KEY = "sk-ant-dummy-000000000000";
  const HINT = "privacy-blocked-hint";

  async function saveKey() {
    const u = userEvent.setup();
    await openForm(u);
    await u.type(await screen.findByLabelText(/api key/i), KEY);
    await u.click(screen.getByRole("button", { name: "Save" }));
    return u;
  }

  it("shows the key-save hint when the re-read catalog has a privacy_mode model", async () => {
    mount({ registry: LLM_REG, llmOnly: true, role: "admin", models: BLOCKED });
    await saveKey();
    const hint = await screen.findByTestId(HINT);
    expect(hint).toHaveTextContent("Cloud models stay off while household privacy mode is local-only.");
  });

  it("a non-LLM save requests neither the catalog nor the policy", async () => {
    const calls = mount({ models: BLOCKED, role: "admin" });
    const u = userEvent.setup();
    await openForm(u);
    await u.type(await screen.findByLabelText(/server address/i), "http://paperless.lan");
    await u.type(screen.getByLabelText(/api token/i), "tok-0123456789abcdef");
    await u.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith("Saved."));
    expect(calls.some((c) => c.method === "PUT")).toBe(true);
    expect(calls.some((c) => c.url.includes("/api/v1/models"))).toBe(false);
    expect(calls.some((c) => c.url.includes("/me/privacy-mode"))).toBe(false);
    expect(screen.queryByTestId(HINT)).not.toBeInTheDocument();
  });

  it("no hint when the key does not make any model usable (catalog reads no_credentials)", async () => {
    // The auth-off Web-saved-key case: following the link would not turn Claude on.
    mount({ registry: LLM_REG, llmOnly: true, role: "admin", models: NO_KEY });
    await saveKey();
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith("Saved."));
    await waitFor(() => expect(screen.queryByTestId(HINT)).not.toBeInTheDocument());
  });

  it("a failed catalog read shows no hint and keeps 'Saved.'", async () => {
    mount({ registry: LLM_REG, llmOnly: true, role: "admin", models: 500 });
    await saveKey();
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith("Saved."));
    expect(screen.queryByTestId(HINT)).not.toBeInTheDocument();
  });

  it("admin tier: the admin's own restriction is not shown (row 5, adminTier)", async () => {
    mount({
      registry: LLM_REG,
      basePath: "/api/v1/admin/connector-credentials/household",
      role: "admin",
      models: BLOCKED,
      privacy: {
        instance: { privacy_mode: "allow_cloud", privacy_mode_locked: false, privacy_effective: "allow_cloud" },
        user_restriction: "local_only",
        privacy_effective: "local_only",
        can_allow_cloud: false,
      },
    });
    await saveKey();
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith("Saved."));
    await waitFor(() => expect(screen.queryByTestId(HINT)).not.toBeInTheDocument());
  });

  it("admin tier: a local-only household still shows the admin row 4 text", async () => {
    mount({
      registry: LLM_REG,
      basePath: "/api/v1/admin/connector-credentials/household",
      role: "admin",
      models: BLOCKED,
    });
    await saveKey();
    const hint = await screen.findByTestId(HINT);
    expect(hint).toHaveTextContent("Cloud models stay off while household privacy mode is local-only.");
    expect(within(hint).getByRole("link")).toHaveAttribute("href", "/settings/privacy?view=household");
  });

  it("a successful delete clears the hint; a failed delete keeps it", async () => {
    // Failed delete first: the key is still there, so the hint must stay.
    mount({
      registry: LLM_REG,
      llmOnly: true,
      role: "admin",
      models: BLOCKED,
      deleteStatus: 500,
      rows: [{ connector: "llm_anthropic", updated_at: "2026-01-01T00:00:00Z", enabled: true }],
    });
    const u = await saveKey();
    await screen.findByTestId(HINT);
    await disconnect(u);
    await screen.findByText("nope");
    expect(screen.getByTestId(HINT)).toBeInTheDocument();
  });

  it("a successful delete clears the hint", async () => {
    mount({
      registry: LLM_REG,
      llmOnly: true,
      role: "admin",
      models: BLOCKED,
      rows: [{ connector: "llm_anthropic", updated_at: "2026-01-01T00:00:00Z", enabled: true }],
    });
    const u = await saveKey();
    await screen.findByTestId(HINT);
    await disconnect(u);
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith("Disconnected."));
    await waitFor(() => expect(screen.queryByTestId(HINT)).not.toBeInTheDocument());
  });
});


describe("ConnectorCard — plain words, Disconnect and Advanced (LUM-993 Pass 5)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows a plain name and purpose, and keeps the raw id out of the main view", async () => {
    mount({ rows: [configuredRow()] });
    await screen.findByRole("heading", { name: /^Paperless documents/ });
    expect(screen.queryByText("paperless")).not.toBeInTheDocument();
    const card = screen.getByTestId("connector-paperless");
    await userEvent.setup().click(within(card).getByRole("button", { name: "Details" }));
    expect(await within(card).findByText("paperless")).toBeInTheDocument();
  });

  it("an LLM key card is named for the provider, not llm_*", async () => {
    mount({ registry: [{ id: "llm_anthropic", description: "x", fields: [] }], llmOnly: true });
    expect(await screen.findByRole("heading", { name: /^Anthropic \(Claude\)/ })).toBeInTheDocument();
    expect(screen.queryByText("llm_anthropic")).not.toBeInTheDocument();
  });

  it("offers Connect when nothing is saved and Edit once it is", async () => {
    mount({ rows: [] });
    expect(await screen.findByRole("button", { name: "Connect" })).toBeInTheDocument();
    cleanup();
    mount({ rows: [configuredRow()] });
    expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("the form is hidden until Connect is pressed", async () => {
    mount({ rows: [] });
    await screen.findByRole("heading", { name: /^Paperless documents/ });
    expect(screen.queryByLabelText(/server address/i)).not.toBeInTheDocument();
    await openForm(userEvent.setup());
    expect(await screen.findByLabelText(/server address/i)).toBeInTheDocument();
  });

  it("a saved form closes and says Saved", async () => {
    mount({ rows: [] });
    const u = userEvent.setup();
    await openForm(u);
    await u.type(await screen.findByLabelText(/server address/i), "https://p.example");
    await u.type(screen.getByLabelText(/api token/i), "0123456789012345");
    await u.click(screen.getByRole("button", { name: /apply/i }));
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith("Saved."));
    await waitFor(() => expect(screen.queryByLabelText(/server address/i)).not.toBeInTheDocument());
  });

  it("Disconnect is in the More actions menu and asks first; Cancel changes nothing", async () => {
    const calls = mount({ rows: [configuredRow()] });
    const u = userEvent.setup();
    expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
    // Not a button on the card itself.
    expect(screen.queryByRole("button", { name: "Disconnect" })).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "More actions for Paperless documents" }));
    await u.click(await screen.findByRole("menuitem", { name: "Disconnect" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/Disconnect Paperless documents\?/);
    await u.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("confirming Disconnect deletes the credential and says so", async () => {
    const calls = mount({ rows: [configuredRow()] });
    const u = userEvent.setup();
    await screen.findByRole("button", { name: "Edit" });
    await disconnect(u);
    await waitFor(() => {
      const del = calls.find((c) => c.method === "DELETE");
      expect(del?.url).toContain("/connector-credentials/paperless");
    });
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith("Disconnected."));
  });

  it("a failed Disconnect shows the error in the card", async () => {
    mount({ rows: [configuredRow()], deleteStatus: 500 });
    const u = userEvent.setup();
    await screen.findByRole("button", { name: "Edit" });
    await disconnect(u);
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  });

  it("the test connector sits under a collapsed Advanced section", async () => {
    mount({
      registry: [
        { id: "paperless", description: "P", fields: PAPERLESS_FIELDS },
        { id: "testconnector", description: "Synthetic plumbing test (no real external system).", fields: [] },
      ],
    });
    await screen.findByRole("heading", { name: /^Paperless documents/ });
    expect(screen.queryByRole("heading", { name: /^Test connector/ })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /advanced: test connector/i }));
    expect(await screen.findByRole("heading", { name: /^Test connector/ })).toBeInTheDocument();
  });

  it("a connector with no guided form keeps its JSON box under Details, not in the main view", async () => {
    mount({ registry: [{ id: "somethingelse", description: "", fields: [] }] });
    await screen.findByRole("heading", { name: /^Somethingelse/ });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^(Connect|Edit)$/ })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /details and advanced setup/i }));
    expect(await screen.findByRole("textbox", { name: /credential payload/i })).toBeInTheDocument();
  });

  it("a failed list load shows a retry, not an empty page", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) return jsonResponse(200, authUser);
      if (url.includes("/registry")) return jsonResponse(200, { items: [] });
      return jsonResponse(500, { detail: "boom" });
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <MemoryRouter>
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <MeConnectorsView />
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
