// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/** Wrap Vitest RTL renders with react-router MemoryRouter (LUM-162). */

import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter, type InitialEntry } from "react-router-dom";

export interface RenderWithRouterOptions extends Omit<RenderOptions, "wrapper"> {
  initialEntries?: InitialEntry[];
  /** Shorthand when mounting a single route (default `/`). */
  route?: InitialEntry;
}

export function renderWithRouter(
  ui: ReactElement,
  { initialEntries, route = "/", ...renderOptions }: RenderWithRouterOptions = {},
): RenderResult {
  const entries = initialEntries ?? [route];
  return render(ui, {
    ...renderOptions,
    wrapper: ({ children }: { children: ReactNode }) => (
      <MemoryRouter initialEntries={entries}>{children}</MemoryRouter>
    ),
  });
}
