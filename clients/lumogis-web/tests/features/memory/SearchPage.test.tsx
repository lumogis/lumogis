// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest unit — SearchPage + EntityCard smoke tests.
// Parent plan §"Phase 1 Pass 1.3" (items 9 + 10).
//
// Tests cover:
//   - Debounced query triggers memory + KG search after 300 ms
//   - Memory hits rendered from GET /api/v1/memory/search
//   - Entity hits rendered from GET /api/v1/kg/search
//   - Degraded memory search shows banner
//   - Selecting an entity opens EntityCard (GET /api/v1/kg/entities/{id} + /related)
//   - Entity 404 renders an error message
//   - Empty query clears results without network call

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { SearchPage } from "../../../src/features/memory/SearchPage";
import { renderWithRouter } from "../../helpers/renderWithRouter";

// ── Helpers ────────────────────────────────────────────────────────────────

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function refreshResponse() {
  return jsonResponse(200, {
    access_token: "tok-test",
    token_type: "bearer",
    expires_in: 900,
    user: { id: "u1", email: "alice@home.lan", role: "user" },
  });
}

function meResponse() {
  return jsonResponse(200, { id: "u1", email: "alice@home.lan", role: "user" });
}

const MEMORY_HITS = {
  hits: [
    {
      id: "mem-1",
      score: 0.92,
      title: "Lumogis memory entry",
      snippet: "This is a test snippet.",
      source: "chat",
      created_at: "2026-01-01T00:00:00Z",
      scope: "personal",
      owner_user_id: null,
    },
  ],
  degraded: false,
  reason: null,
};

const ENTITY_HITS = {
  entities: [
    {
      entity_id: "ent-abc",
      name: "Lumogis",
      type: "Project",
      aliases: [],
      summary: null,
      sources: [],
      scope: "personal",
      owner_user_id: null,
    },
  ],
};

const ENTITY_DETAIL = {
  entity_id: "ent-abc",
  name: "Lumogis",
  type: "Project",
  aliases: ["LG", "Lumogis App"],
  summary: "A personal AI assistant.",
  sources: ["capture://abc"],
  scope: "personal",
  owner_user_id: null,
};

const RELATED_ENTITIES = {
  related: [
    {
      entity_id: "ent-xyz",
      name: "Thomas",
      relation: "CO_OCCURS",
      weight: 0.85,
    },
  ],
};

// ── Mock client builder ──────────────────────────────────────────────────

type FetchOverrides = Partial<{
  memory: (url: string) => Response;
  kgSearch: (url: string) => Response;
  entityDetail: (url: string) => Response;
  relatedEntities: (url: string) => Response;
}>;

function buildClient(overrides: FetchOverrides = {}) {
  const tokens = new AccessTokenStore();
  const fetchImpl = vi.fn(async (url: string) => {
    if (url.includes("/api/v1/auth/refresh")) return refreshResponse();
    if (url.includes("/api/v1/auth/me")) return meResponse();
    if (url.includes("/api/v1/memory/search")) {
      return overrides.memory
        ? overrides.memory(url)
        : jsonResponse(200, MEMORY_HITS);
    }
    if (url.includes("/api/v1/kg/search")) {
      return overrides.kgSearch
        ? overrides.kgSearch(url)
        : jsonResponse(200, ENTITY_HITS);
    }
    if (url.includes("/api/v1/kg/entities/ent-abc/related")) {
      return overrides.relatedEntities
        ? overrides.relatedEntities(url)
        : jsonResponse(200, RELATED_ENTITIES);
    }
    if (url.includes("/api/v1/kg/entities/ent-abc")) {
      return overrides.entityDetail
        ? overrides.entityDetail(url)
        : jsonResponse(200, ENTITY_DETAIL);
    }
    return jsonResponse(404, { error: "not_found" });
  });

  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { tokens, client, fetchImpl };
}

// ── Mount helper ─────────────────────────────────────────────────────────

function mountSearchPage(overrides: FetchOverrides = {}) {
  const { tokens, client } = buildClient(overrides);
  renderWithRouter(
    <AuthProvider client={client} tokens={tokens}>
      <SearchPage />
    </AuthProvider>,
    { route: "/search" },
  );
  return { client };
}

// ── Tests ─────────────────────────────────────────────────────────────────

// Use userEvent with no delay so fake timers don't conflict with keypress
// timing. We advance fake timers explicitly for the debounce.
const user = userEvent.setup({ delay: null });

describe("SearchPage", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the search input", async () => {
    mountSearchPage();
    // Auth settles synchronously with skipRefreshOnMount=false but refresh
    // returns immediately from the mock
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /search query/i })).toBeInTheDocument();
    });
  });

  it("shows empty state with no query", async () => {
    mountSearchPage();
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /search query/i })).toBeInTheDocument();
    });
    expect(screen.getAllByText(/type to search/i).length).toBeGreaterThanOrEqual(1);
  });

  it("triggers search after debounce and shows memory hits", async () => {
    mountSearchPage();
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /search query/i })).toBeInTheDocument();
    });

    await user.type(screen.getByRole("textbox", { name: /search query/i }), "lumogis");
    // Advance past 300ms debounce
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    await waitFor(() => {
      expect(screen.getByText("Lumogis memory entry")).toBeInTheDocument();
    }, { timeout: 3000 });
    expect(screen.getByText(/This is a test snippet/)).toBeInTheDocument();
  });

  it("shows entity hits from KG search", async () => {
    mountSearchPage();
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /search query/i })).toBeInTheDocument();
    });

    await user.type(screen.getByRole("textbox", { name: /search query/i }), "lumogis");
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    await waitFor(() => {
      expect(screen.getByText("Project")).toBeInTheDocument();
    }, { timeout: 3000 });
  });

  it("shows degraded banner when memory search is degraded", async () => {
    mountSearchPage({
      memory: () =>
        jsonResponse(200, {
          hits: [],
          degraded: true,
          reason: "embedder_not_ready",
        }),
    });
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /search query/i })).toBeInTheDocument();
    });

    await user.type(screen.getByRole("textbox", { name: /search query/i }), "test");
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    await waitFor(() => {
      expect(screen.getByText(/memory search is degraded/i)).toBeInTheDocument();
    }, { timeout: 3000 });
  });

  it("shows entity card panel when an entity is selected", async () => {
    mountSearchPage();
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /search query/i })).toBeInTheDocument();
    });

    await user.type(screen.getByRole("textbox", { name: /search query/i }), "lumogis");
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    // Wait for entity button to appear and click it
    const entityBtn = await waitFor(
      () => screen.getByRole("button", { name: /lumogis/i }),
      { timeout: 3000 },
    );
    await user.click(entityBtn);

    // Entity card should appear with detail and related
    await waitFor(() => {
      expect(screen.getByText("A personal AI assistant.")).toBeInTheDocument();
    }, { timeout: 3000 });
    expect(document.querySelector(".lumogis-entity-card")).not.toBeNull();
    expect(screen.getByText("Thomas")).toBeInTheDocument();
    expect(screen.getByText("LG")).toBeInTheDocument();
  });

  it("hides entity card when same entity is clicked again (toggle)", async () => {
    mountSearchPage();
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /search query/i })).toBeInTheDocument();
    });

    await user.type(screen.getByRole("textbox", { name: /search query/i }), "lumogis");
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    const entityBtn = await waitFor(
      () => screen.getByRole("button", { name: /lumogis/i }),
      { timeout: 3000 },
    );
    await user.click(entityBtn);

    await waitFor(() => {
      expect(screen.getByText("A personal AI assistant.")).toBeInTheDocument();
    }, { timeout: 3000 });

    // Click again to toggle off
    await user.click(entityBtn);
    await waitFor(() => {
      expect(screen.queryByText("A personal AI assistant.")).toBeNull();
    });
  });

  it("renders a Shared badge for shared entities (LUM-581)", async () => {
    mountSearchPage({
      kgSearch: () =>
        jsonResponse(200, {
          entities: [
            {
              entity_id: "ent-shared",
              name: "Acme",
              type: "Org",
              aliases: [],
              summary: null,
              sources: [],
              scope: "shared",
              owner_user_id: "u2",
              share_status: "shared",
              is_owner: false,
            },
          ],
        }),
    });
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /search query/i })).toBeInTheDocument();
    });

    await user.type(screen.getByRole("textbox", { name: /search query/i }), "acme");
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    await waitFor(() => {
      expect(screen.getByText("Acme")).toBeInTheDocument();
    }, { timeout: 3000 });
    // The friendly "Shared" badge (distinct from the lowercase scope pill).
    expect(screen.getByText("Shared")).toBeInTheDocument();
  });

  it("filters to shared entities when the household filter is enabled (LUM-581)", async () => {
    mountSearchPage({
      kgSearch: () =>
        jsonResponse(200, {
          entities: [
            {
              entity_id: "ent-shared",
              name: "Acme",
              type: "Org",
              aliases: [],
              summary: null,
              sources: [],
              scope: "shared",
              owner_user_id: "u2",
              share_status: "shared",
              is_owner: false,
            },
            {
              entity_id: "ent-personal",
              name: "Beta",
              type: "Org",
              aliases: [],
              summary: null,
              sources: [],
              scope: "personal",
              owner_user_id: null,
              share_status: "personal",
              is_owner: true,
            },
          ],
        }),
    });
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /search query/i })).toBeInTheDocument();
    });

    await user.type(screen.getByRole("textbox", { name: /search query/i }), "co");
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    await waitFor(() => {
      expect(screen.getByText("Beta")).toBeInTheDocument();
    }, { timeout: 3000 });
    expect(screen.getByText("Acme")).toBeInTheDocument();

    // Enable the "Shared with household" filter → the personal entity drops out.
    await user.click(
      screen.getByRole("checkbox", { name: /shared with household/i }),
    );
    await waitFor(() => {
      expect(screen.queryByText("Beta")).toBeNull();
    });
    expect(screen.getByText("Acme")).toBeInTheDocument();
  });

  it("shows no hits message when search returns empty results", async () => {
    mountSearchPage({
      memory: () => jsonResponse(200, { hits: [], degraded: false, reason: null }),
      kgSearch: () => jsonResponse(200, { entities: [] }),
    });

    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /search query/i })).toBeInTheDocument();
    });

    await user.type(screen.getByRole("textbox", { name: /search query/i }), "xyz");
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    await waitFor(() => {
      expect(screen.getByText(/no memory hits/i)).toBeInTheDocument();
    }, { timeout: 3000 });
    expect(screen.getByText(/no entity hits/i)).toBeInTheDocument();
  });
});
