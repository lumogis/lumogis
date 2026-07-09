// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-512 — graceful service-degradation banners.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ServiceDegradationBanner } from "../../../src/features/_shared/ServiceDegradationBanner";
import type { ServiceHealth } from "../../../src/features/_shared/useServiceHealth";

function makeHealth(overrides: Partial<ServiceHealth> = {}): ServiceHealth {
  return {
    health: undefined,
    isLoading: false,
    isOllamaDown: false,
    isQdrantDown: false,
    isGraphDown: false,
    refresh: () => {},
    ...overrides,
  };
}

describe("ServiceDegradationBanner (LUM-512)", () => {
  it("renders nothing when all services are healthy", () => {
    const { container } = render(<ServiceDegradationBanner health={makeHealth()} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows a hard-fail alert when Ollama is down (chat paused)", () => {
    render(<ServiceDegradationBanner health={makeHealth({ isOllamaDown: true })} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/local ai unavailable/i);
    expect(alert).toHaveTextContent(/messages may fail/i);
    expect(alert).toHaveTextContent(/lumogis doctor/i);
  });

  it("shows a degraded (non-alert) status when Qdrant is down", () => {
    render(<ServiceDegradationBanner health={makeHealth({ isQdrantDown: true })} />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(/document search unavailable/i);
  });

  it("shows a degraded status when the knowledge graph is down", () => {
    render(<ServiceDegradationBanner health={makeHealth({ isGraphDown: true })} />);
    expect(screen.getByRole("status")).toHaveTextContent(/knowledge graph temporarily unavailable/i);
  });

  it("stacks multiple banners when several services are down", () => {
    render(
      <ServiceDegradationBanner
        health={makeHealth({ isOllamaDown: true, isQdrantDown: true })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/local ai unavailable/i);
    expect(screen.getByRole("status")).toHaveTextContent(/document search unavailable/i);
  });
});
