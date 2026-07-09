// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-211/512 code-review fix: a failed full-entity fetch must still surface an
// error + retry even when a partial `initialCard` (from search) is displayed —
// rather than silently leaving stale data.

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { ApiClient } from "../../../src/api/client";
import type { EntityCard } from "../../../src/api/search";
import { EntityCardPanel } from "../../../src/features/memory/EntityCard";

const getEntityMock = vi.fn();
const getRelatedMock = vi.fn();

vi.mock("../../../src/api/search", () => ({
  getEntity: (...a: unknown[]) => getEntityMock(...a),
  getRelatedEntities: (...a: unknown[]) => getRelatedMock(...a),
  // LUM-581 — EntityCard now renders <EntityShareToggle>, which imports these.
  isEntityShared: (s: string | undefined) => s === "shared",
  publishEntity: vi.fn(),
  unpublishEntity: vi.fn(),
}));

const client = {} as ApiClient;

const INITIAL: EntityCard = {
  id: "e1",
  entity_id: "e1",
  name: "Ada Lovelace",
  type: "person",
  scope: "personal",
  summary: null,
  aliases: [],
  sources: [],
  owner_user_id: null,
} as unknown as EntityCard;

describe("EntityCardPanel — partial-card fetch failure (LUM-512 review fix)", () => {
  beforeEach(() => {
    getEntityMock.mockReset();
    getRelatedMock.mockReset();
  });

  it("shows an error + retry while still rendering the initialCard when the fetch fails", async () => {
    getEntityMock.mockRejectedValue(new Error("boom"));
    getRelatedMock.mockRejectedValue(new Error("boom"));

    render(
      <MemoryRouter>
        <EntityCardPanel entityId="e1" client={client} initialCard={INITIAL} />
      </MemoryRouter>,
    );

    // Partial data from search is still shown…
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    // …and the failure is surfaced with an actionable retry (not silently dropped).
    await waitFor(() => {
      const alert = screen.getByRole("alert");
      expect(alert).toBeInTheDocument();
      expect(alert).toHaveTextContent(/unavailable|not found/i);
    });
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});
