// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-853 — permanent notice shown under the shell header while Lumogis Web runs in
// single-user mode (Core has sign-in off and answered /auth/me without a credential).
//
// No live region (present from the shell's first render, never changes), no dismiss control
// (a security notice the operator removes by turning sign-in on), no browser storage.
// The copy is the same for every hostname on purpose, always the strongest version: a softer
// `localhost` variant would be false (Docker publishes on all interfaces), and a stronger
// non-loopback variant cannot tell a LAN name from a public one without a public-suffix guess.

/** Public handbook anchor; must match the quickstart heading "Turn on sign-in". */
export const TURN_ON_SIGN_IN_DOC_URL =
  "https://github.com/lumogis/lumogis/blob/main/docs/deployment/quickstart.md#turn-on-sign-in";

/** Public handbook page on safe remote access. */
export const REMOTE_ACCESS_SAFETY_DOC_URL =
  "https://github.com/lumogis/lumogis/blob/main/docs/deployment/remote-access.md#security-guidance";

export const SINGLE_USER_BANNER_COPY =
  "Sign-in is off. Anyone who can reach this address uses Lumogis as its admin: people on this device, on your network, and on the internet if the address is public.";

export interface SingleUserBannerProps {
  visible: boolean;
}

export function SingleUserBanner({ visible }: SingleUserBannerProps): JSX.Element | null {
  if (!visible) return null;

  return (
    <aside
      className="lumogis-single-user-banner"
      aria-label="Sign-in is off"
      data-testid="lumogis-single-user-banner"
    >
      <p className="lumogis-offline-banner__text">
        {SINGLE_USER_BANNER_COPY}{" "}
        <a href={TURN_ON_SIGN_IN_DOC_URL} target="_blank" rel="noopener noreferrer">
          Turn on sign-in
        </a>{" "}
        ·{" "}
        <a href={REMOTE_ACCESS_SAFETY_DOC_URL} target="_blank" rel="noopener noreferrer">
          Safe remote access
        </a>
      </p>
    </aside>
  );
}
