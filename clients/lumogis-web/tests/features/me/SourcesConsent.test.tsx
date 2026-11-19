// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-837 chunk B — the add flow and both consent gates, through the UI.
//
// `sourceConsent.test.ts` pins the state machine; this file pins that the view
// is actually wired to it — that the flags reaching the wire are the ones the
// reducer computed, that each refusal renders through its own shipped parser,
// and that the credentials link points somewhere that exists.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AddSourcePanel } from "../../../src/features/me/AddSourcePanel";
import { STARVATION_CODE } from "../../../src/features/signals/externalConsumerWarning";
import { OVERLAP_CODE } from "../../../src/features/email/paperlessOverlapWarning";
import { jsonResponse } from "../../helpers/jsonResponse";

function refusalBody(code: string, extra: Record<string, unknown> = {}) {
  return { detail: { code, message: "m", alternative: "a", recovery: "r", ...extra } };
}

const STARVATION = refusalBody(STARVATION_CODE);
const OVERLAP = refusalBody(OVERLAP_CODE, {
  mailboxes: [{ source_name: "Personal", rule_count: 3 }],
});

/** `postResponses` is consumed one per `confirm: true` call, so a test can
 *  script a refusal sequence. Bodies sent are captured for assertion. */
function setup(
  opts: { postResponses?: Response[]; credentials?: unknown[]; previewResponse?: Response } = {},
) {
  const user = { id: "admin", email: "admin@home.lan", role: "admin" as const };
  const bodies: Record<string, unknown>[] = [];
  const queue = [...(opts.postResponses ?? [])];
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/auth/me")) return jsonResponse(200, user);
    if (url.includes("/api/v1/me/connector-credentials")) {
      return jsonResponse(200, { items: opts.credentials ?? [] });
    }
    if (url.includes("/sources") && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      if (body.confirm === false) {
        return (
          opts.previewResponse ??
          // No `reachable`: the paperless preview branch never contacts the
          // host, so the route genuinely does not send the key. Mocking it here
          // would test against a contract the server does not have — and it
          // makes this default the real "older backend omits it" case.
          jsonResponse(200, {
            source_type: "paperless",
            url: body.url,
            preview_items: [],
          })
        );
      }
      bodies.push(body);
      return (
        queue.shift() ??
        jsonResponse(200, {
          status: "created",
          source_id: "src-new",
          source_type: "paperless",
          url: body.url,
          preview_items: [],
        })
      );
    }
    return jsonResponse(404, { detail: "not found" });
  });
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { client, tokens, bodies };
}

function renderPanel(s: ReturnType<typeof setup>) {
  render(
    <AuthProvider client={s.client} tokens={s.tokens} skipRefreshOnMount>
      <AddSourcePanel onAdded={() => {}} />
    </AuthProvider>,
  );
}

/** The kind picker is a Base UI Select: open it, then pick an option. */
async function chooseKind(user: ReturnType<typeof userEvent.setup>, kind: "detect" | "paperless") {
  await user.click(await screen.findByRole("combobox", { name: "What are you adding?" }));
  await user.click(
    await screen.findByRole("option", { name: kind === "paperless" ? "Paperless" : "Detect automatically" }),
  );
}

/** Choose Paperless, type an address, and get to the point where Add exists. */
async function reachAdd(user: ReturnType<typeof userEvent.setup>) {
  await chooseKind(user, "paperless");
  await user.type(screen.getByLabelText("Paperless address"), "https://paperless.example.com");
  await user.click(screen.getByRole("button", { name: "Check" }));
  return screen.findByRole("button", { name: "Add" });
}

describe("AddSourcePanel — the consent sequence", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("the first Add press sends neither acknowledgement flag", async () => {
    // The acknowledgement is a mutation parameter, never state read at call
    // time. A component that always sent `true` would turn the gate into a
    // dismissible banner in JSON.
    const s = setup();
    const user = userEvent.setup();
    renderPanel(s);
    await user.click(await reachAdd(user));
    await waitFor(() => expect(s.bodies).toHaveLength(1));
    expect(s.bodies[0].acknowledge_external_consumer).toBeUndefined();
    expect(s.bodies[0].acknowledge_paperless_overlap).toBeUndefined();
  });

  it("overlap-only: the re-post carries overlap and NOT starvation", async () => {
    // No folder-sorting mailbox — the commonest paperless household, and the
    // path the plan's original consent table omitted entirely.
    const s = setup({ postResponses: [jsonResponse(422, OVERLAP)] });
    const user = userEvent.setup();
    renderPanel(s);
    await user.click(await reachAdd(user));

    const card = await screen.findByRole("alert");
    expect(card.textContent).toContain("Personal");
    expect(card.textContent).toContain("3 rules");

    await user.click(screen.getByRole("button", { name: "Connect anyway" }));
    await waitFor(() => expect(s.bodies).toHaveLength(2));
    expect(s.bodies[1].acknowledge_paperless_overlap).toBe(true);
    expect(s.bodies[1].acknowledge_external_consumer).toBeUndefined();
  });

  it("starvation-only: the re-post carries starvation and NOT overlap", async () => {
    const s = setup({ postResponses: [jsonResponse(422, STARVATION)] });
    const user = userEvent.setup();
    renderPanel(s);
    await user.click(await reachAdd(user));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Connect anyway" }));
    await waitFor(() => expect(s.bodies).toHaveLength(2));
    expect(s.bodies[1].acknowledge_external_consumer).toBe(true);
    expect(s.bodies[1].acknowledge_paperless_overlap).toBeUndefined();
  });

  it("two-arm: refuse, acknowledge, refuse, acknowledge, created — third body carries BOTH", async () => {
    // The test whose absence let an infinite loop through review round 1: the
    // server checks the starvation arm first and unconditionally on every
    // request, so a re-post that drops that flag re-triggers the arm the user
    // already answered.
    const s = setup({
      postResponses: [jsonResponse(422, STARVATION), jsonResponse(422, OVERLAP)],
    });
    const user = userEvent.setup();
    renderPanel(s);
    await user.click(await reachAdd(user));

    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Connect anyway" }));
    await waitFor(() => expect(s.bodies).toHaveLength(2));

    await screen.findByText(/Personal/);
    await user.click(screen.getByRole("button", { name: "Connect anyway" }));
    await waitFor(() => expect(s.bodies).toHaveLength(3));

    expect(s.bodies[1]).toMatchObject({ acknowledge_external_consumer: true });
    expect(s.bodies[1].acknowledge_paperless_overlap).toBeUndefined();
    expect(s.bodies[2]).toMatchObject({
      acknowledge_external_consumer: true,
      acknowledge_paperless_overlap: true,
    });
    expect(await screen.findByText("Source added.")).toBeInTheDocument();
  });

  it("each refusal renders through its own parser — never one merged control", async () => {
    const s = setup({ postResponses: [jsonResponse(422, STARVATION)] });
    const user = userEvent.setup();
    renderPanel(s);
    await user.click(await reachAdd(user));
    const card = await screen.findByRole("alert");
    // The starvation card must not carry the overlap card's mailbox list.
    expect(card.textContent).toContain("miss some mail");
    expect(card.textContent).not.toContain("file these twice");
    expect(screen.getAllByRole("button", { name: "Connect anyway" })).toHaveLength(1);
  });

  it("a refusal with an unrecognised code renders a generic failure, not silence", async () => {
    const s = setup({ postResponses: [jsonResponse(422, refusalBody("something_new"))] });
    const user = userEvent.setup();
    renderPanel(s);
    await user.click(await reachAdd(user));
    expect(await screen.findByText("We couldn't add that source — try again.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect anyway" })).not.toBeInTheDocument();
  });

  it("never renders the server's exception string on a 500", async () => {
    const s = setup({
      postResponses: [
        jsonResponse(500, { detail: "Could not save source: postgres://u:pw@db.internal/l" }),
      ],
    });
    const user = userEvent.setup();
    renderPanel(s);
    await user.click(await reachAdd(user));
    await screen.findByText("We couldn't add that source — try again.");
    expect(document.body.textContent).not.toContain("postgres://");
    expect(document.body.textContent).not.toContain("db.internal");
  });

  it("changing the URL after acknowledging clears the flags", async () => {
    const s = setup({ postResponses: [jsonResponse(422, OVERLAP)] });
    const user = userEvent.setup();
    renderPanel(s);
    await user.click(await reachAdd(user));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Connect anyway" }));
    await waitFor(() => expect(s.bodies).toHaveLength(2));

    // A new address is a different question; the next attempt starts clean.
    await user.type(screen.getByLabelText("Paperless address"), "/changed");
    await user.click(screen.getByRole("button", { name: "Check" }));
    await user.click(await screen.findByRole("button", { name: "Add" }));
    await waitFor(() => expect(s.bodies).toHaveLength(3));
    expect(s.bodies[2].acknowledge_paperless_overlap).toBeUndefined();
  });
});

describe("AddSourcePanel — the credential step", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("links to /settings/connections, in a new tab", async () => {
    // NOT /admin/connector-credentials: that page's Household and System tabs
    // write tiers `load_connection` never reads. And NOT bare /connectors:
    // `MeConnectorsView` is a nested route under /me, so /connectors matches
    // nothing and falls through to the catch-all redirect to /chat.
    const s = setup();
    const user = userEvent.setup();
    renderPanel(s);
    await chooseKind(user, "paperless");
    const link = await screen.findByRole("link", { name: "Add them" });
    expect(link).toHaveAttribute("href", "/settings/connections");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("says when credentials are missing, states the consequence, and still allows Add", async () => {
    const s = setup({ credentials: [] });
    const user = userEvent.setup();
    renderPanel(s);
    // Reach the state where Add exists, without pressing it — a successful add
    // resets the form and takes the button with it.
    await reachAdd(user);
    expect(screen.getByText(/No paperless credentials yet/)).toBeInTheDocument();
    expect(screen.getByText(/cannot poll/)).toBeInTheDocument();
    // The flow tells; it does not block. A source with no credential is a real,
    // recoverable state, and a disabled button with no explanation is the defect
    // class this feature exists to fix.
    expect(screen.getByRole("button", { name: "Add" })).not.toBeDisabled();
  });

  it("says when credentials exist, and labels the link as changing them", async () => {
    const s = setup({
      credentials: [
        { connector: "paperless", instance: "", created_at: "x", updated_at: "2026-08-18T09:00:00Z" },
      ],
    });
    const user = userEvent.setup();
    renderPanel(s);
    await chooseKind(user, "paperless");
    expect(await screen.findByText(/Paperless credentials saved/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Change them" })).toBeInTheDocument();
  });

  it("shows nothing and blocks nothing when the presence read fails", async () => {
    const user = userEvent.setup();
    const tokens = new AccessTokenStore();
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return jsonResponse(200, { id: "a", email: "a@b.c", role: "admin" });
      }
      if (url.includes("/api/v1/me/connector-credentials")) return jsonResponse(500, {});
      return jsonResponse(200, { source_type: "paperless", url: "u", preview_items: [] });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <AddSourcePanel onAdded={() => {}} />
      </AuthProvider>,
    );
    await chooseKind(user, "paperless");
    await waitFor(() =>
      expect(screen.queryByText(/No paperless credentials yet/)).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(/Paperless credentials saved/)).not.toBeInTheDocument();
  });
});

describe("AddSourcePanel — the type selector and the preview", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("does NOT offer IMAP — the route has no branch for it", async () => {
    // `SourceRequest.source_type` is `Literal["paperless", "imap"] | None`, but
    // `POST /sources` has no imap branch: an `imap` value falls through to
    // `_detect_source`, which would run feed detection against an `imap://` URL
    // and store a nonsense `page` source.
    const s = setup();
    renderPanel(s);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("combobox", { name: "What are you adding?" }));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(options).toEqual(["Detect automatically", "Paperless"]);
    expect(options.join(" ").toLowerCase()).not.toContain("imap");
  });

  it("the paperless step claims only that the address looks valid", async () => {
    // `confirm=false` for paperless runs the base-URL validator and stops: it
    // never contacts the instance, never uses the credential, and always
    // returns empty `preview_items`. The copy must not imply otherwise.
    const s = setup();
    const user = userEvent.setup();
    renderPanel(s);
    await reachAdd(user);
    const text = document.body.textContent ?? "";
    expect(text).toContain("The address looks valid");
    expect(text).not.toMatch(/\bconnected\b/i);
    expect(text).not.toMatch(/\bverified\b/i);
    expect(text).not.toMatch(/\bworking\b/i);
    // An empty paperless preview is the EXPECTED state, never "no items found".
    expect(text).not.toMatch(/no items/i);
  });

  it("does not fire the preview on typing — only on the explicit button", async () => {
    const s = setup();
    const user = userEvent.setup();
    renderPanel(s);
    await chooseKind(user, "paperless");
    await user.type(screen.getByLabelText("Paperless address"), "https://paperless.example.com");
    // A live outbound fetch that can probe several paths off the site root is
    // not something to fire per keystroke.
    expect(screen.queryByRole("button", { name: "Add" })).not.toBeInTheDocument();
  });
});

describe("AddSourcePanel — reachability (LUM-841)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("says it couldn't reach the address, NOT that it will watch the page", async () => {
    // The whole point of LUM-841: a host that did not answer used to render as
    // "No feed found, so we'll watch the page itself" — a reassuring lie that
    // sent people away with a source polling nothing.
    const s = setup({
      previewResponse: jsonResponse(200, {
        source_type: "page",
        url: "https://typo.invalid",
        preview_items: [],
        reachable: false,
      }),
    });
    const user = userEvent.setup();
    renderPanel(s);
    await chooseKind(user, "detect");
    await user.type(screen.getByLabelText("Address"), "https://typo.invalid");
    await user.click(screen.getByRole("button", { name: "Check" }));

    expect(await screen.findByText(/couldn't reach that address/i)).toBeInTheDocument();
    expect(screen.queryByText(/watch the page itself/i)).not.toBeInTheDocument();
  });

  it("still allows Add on an unreachable host — the flow tells, it does not block", async () => {
    const s = setup({
      previewResponse: jsonResponse(200, {
        source_type: "page",
        url: "https://down.example.com",
        preview_items: [],
        reachable: false,
      }),
    });
    const user = userEvent.setup();
    renderPanel(s);
    await chooseKind(user, "detect");
    await user.type(screen.getByLabelText("Address"), "https://down.example.com");
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByRole("button", { name: "Add" })).not.toBeDisabled();
  });

  it("a REACHABLE page with no feed still reads as a page source, not a failure", async () => {
    // Identical `preview_items` to the unreachable case, opposite meaning.
    const s = setup({
      previewResponse: jsonResponse(200, {
        source_type: "page",
        url: "https://example.com/sparse",
        preview_items: [],
        reachable: true,
      }),
    });
    const user = userEvent.setup();
    renderPanel(s);
    await chooseKind(user, "detect");
    await user.type(screen.getByLabelText("Address"), "https://example.com/sparse");
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/watch the page itself/i)).toBeInTheDocument();
    expect(screen.queryByText(/couldn't reach/i)).not.toBeInTheDocument();
  });

  it("an older backend that omits `reachable` stays neutral rather than crying wolf", async () => {
    const s = setup({
      previewResponse: jsonResponse(200, {
        source_type: "page",
        url: "https://example.com",
        preview_items: [],
      }),
    });
    const user = userEvent.setup();
    renderPanel(s);
    await chooseKind(user, "detect");
    await user.type(screen.getByLabelText("Address"), "https://example.com");
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/watch the page itself/i)).toBeInTheDocument();
    expect(screen.queryByText(/couldn't reach/i)).not.toBeInTheDocument();
  });
});

describe("AddSourcePanel — the per-user refusals (LUM-839)", () => {
  it("a rate-limited preview does NOT tell the user to try again", async () => {
    // The generic preview fallback is "We couldn't check that address — try
    // again", which is the precise opposite of the correct advice for a 429 —
    // and preview is the path the limiter fires on most often. A test that
    // only asserted "some message renders" would pass against that bug, so
    // this asserts BOTH that the right copy appears and the wrong copy does not.
    const s = setup({
      previewResponse: jsonResponse(429, { detail: { code: "rate_limited" } }),
    });
    renderPanel(s);
    const user = userEvent.setup();
    await chooseKind(user, "paperless");
    await user.type(
      screen.getByLabelText("Paperless address"),
      "https://paperless.example.com",
    );
    await user.click(screen.getByRole("button", { name: "Check" }));

    await waitFor(() =>
      expect(screen.getByText(/Too many attempts/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/couldn't check that address/i)).toBeNull();
  });

  it("the cap refusal shows the server's message, keyed on code", async () => {
    const s = setup({
      postResponses: [
        jsonResponse(422, {
          detail: {
            code: "source_cap_reached",
            message:
              "You've reached the limit of 100 sources. Delete one to add another.",
          },
        }),
      ],
    });
    renderPanel(s);
    const user = userEvent.setup();
    await user.click(await reachAdd(user));
    await waitFor(() =>
      expect(
        screen.getByText(/reached the limit of 100 sources/i),
      ).toBeInTheDocument(),
    );
  });
});
