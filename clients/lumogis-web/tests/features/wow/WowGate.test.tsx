// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { WowGate } from "../../../src/features/wow/WowGate";

const mockUseWowState = vi.fn();

vi.mock("../../../src/features/wow/useWowState", () => ({
  useWowState: (...args: unknown[]) => mockUseWowState(...args),
}));

function readyState(overrides: Record<string, unknown> = {}) {
  return {
    query: {
      status: "success" as const,
      data: {
        entities_ready: true,
        top_entities: [
          {
            entity_id: "e1",
            name: "Alice",
            entity_type: "Person",
            mention_count: 2,
            scope: "personal" as const,
          },
        ],
        wow_dismissed_at: null,
        onboarding_completed_at: "2026-01-01T00:00:00Z",
        ...overrides,
      },
    },
    dismissWow: vi.fn(),
    dismissError: null,
    isDismissing: false,
  };
}

function renderGate() {
  const tokens = new AccessTokenStore();
  tokens.set("tok");
  const client = new ApiClient({
    tokens,
    fetchImpl: vi.fn(async () => new Response("{}")) as unknown as typeof fetch,
  });
  return render(
    <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
      <MemoryRouter>
        <WowGate onPrefillComposer={vi.fn()} />
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("WowGate", () => {
  beforeEach(() => {
    mockUseWowState.mockReset();
  });

  it("does not render cards when wow_dismissed_at is set", () => {
    mockUseWowState.mockReturnValue(
      readyState({ wow_dismissed_at: "2026-06-01T00:00:00Z" }),
    );
    renderGate();
    expect(screen.queryByTestId("wow-gate")).toBeNull();
  });

  it("does not render cards when onboarding_completed_at is null", () => {
    mockUseWowState.mockReturnValue(
      readyState({ onboarding_completed_at: null }),
    );
    renderGate();
    expect(screen.queryByTestId("wow-gate")).toBeNull();
  });

  it("renders guided and discovery cards when eligible", () => {
    mockUseWowState.mockReturnValue(readyState());
    renderGate();
    expect(screen.getByTestId("wow-gate")).toBeInTheDocument();
    expect(screen.getByTestId("wow-guided-card")).toBeInTheDocument();
    expect(screen.getByTestId("wow-discovery-card")).toBeInTheDocument();
  });
});
