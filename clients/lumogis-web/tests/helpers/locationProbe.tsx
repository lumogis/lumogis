// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-282. A route assertion needs something inside the Router to read
// `useLocation()` from — there is no way to assert "we navigated" from outside
// it. This chunk needed one in two files and wrote it twice; `AdminPage.test`
// had already written a third for its own toast state. One home, so the fourth
// person does not write a fourth.
//
// `data-*` attributes rather than text so a probe can sit in a tree whose text
// is being asserted without polluting it.

import { useLocation } from "react-router-dom";

export const LOCATION_PROBE_TEST_ID = "location-probe";

/**
 * Renders the current pathname, plus any `location.state` keys the caller
 * names, as data attributes.
 *
 * ```tsx
 * <LocationProbe stateKeys={["fromOnboarding"]} />
 * screen.getByTestId("location-probe").textContent            // "/me/sources"
 * screen.getByTestId("location-probe").dataset.fromOnboarding // "true" | "false"
 * ```
 */
export function LocationProbe({
  stateKeys = [],
}: {
  stateKeys?: readonly string[];
}): JSX.Element {
  const loc = useLocation();
  const state = (loc.state ?? null) as Record<string, unknown> | null;
  const data: Record<string, string> = {};
  for (const key of stateKeys) {
    data[`data-${key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`] = String(
      state?.[key] === true,
    );
  }
  return (
    <p data-testid={LOCATION_PROBE_TEST_ID} {...data}>
      {loc.pathname}
    </p>
  );
}
