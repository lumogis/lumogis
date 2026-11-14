// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-687 (674b) — device-flow connect panel: shows the user_code, polls until
// connected, treats a 429 as "still waiting", and stops polling on unmount.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ApiClient, ApiError } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import {
  OAuthDevicePanel,
  type OAuthDeviceApi,
} from "../../../src/features/signals/OAuthDevicePanel";
import type { OAuthDevicePollResponse } from "../../../src/api/emailSources";

function dummyClient(): ApiClient {
  return new ApiClient({ tokens: new AccessTokenStore(), fetchImpl: (() => {
    throw new Error("no network in this test");
  }) as unknown as typeof fetch });
}

const FLOW = {
  flow_id: "F1",
  user_code: "WXYZ-1234",
  verification_uri: "https://ms.example/dev",
  verification_uri_complete: "https://ms.example/dev?otc=WXYZ-1234",
  interval: 1,
  expires_in: 900,
};

function renderPanel(api: OAuthDeviceApi, onConnected = vi.fn(), onCancel = vi.fn()) {
  render(
    <OAuthDevicePanel
      client={dummyClient()}
      provider="outlook"
      providerLabel="Outlook.com"
      email="a@b.com"
      mailboxLabel="work"
      onConnected={onConnected}
      onCancel={onCancel}
      api={api}
    />,
  );
  return { onConnected, onCancel };
}

describe("OAuthDevicePanel", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows the user code and verification link after start", async () => {
    const start = vi.fn().mockResolvedValue(FLOW);
    const poll = vi.fn().mockResolvedValue({ status: "pending" } as OAuthDevicePollResponse);
    renderPanel({ start, poll });

    expect(await screen.findByLabelText(/device code/i)).toHaveTextContent("WXYZ-1234");
    const link = screen.getByRole("link", { name: /Outlook\.com sign-in/i });
    expect(link).toHaveAttribute("href", FLOW.verification_uri_complete);
    // Start is called with the registry provider id + the mailbox body.
    expect(start).toHaveBeenCalledWith(expect.anything(), "outlook", {
      email: "a@b.com",
      mailbox_label: "work",
    });
  });

  // LUM-691 — import-scope parity: forwarded into the start body only when set.
  it("forwards importScope/importSince/importBefore into the start body when provided", async () => {
    const start = vi.fn().mockResolvedValue(FLOW);
    const poll = vi.fn().mockResolvedValue({ status: "pending" });
    render(
      <OAuthDevicePanel
        client={dummyClient()}
        provider="outlook"
        providerLabel="Outlook.com"
        email="a@b.com"
        mailboxLabel="work"
        importScope="custom"
        importSince="2026-01-01"
        importBefore="2026-06-01"
        onConnected={vi.fn()}
        api={{ start, poll }}
      />,
    );
    await screen.findByLabelText(/device code/i);
    expect(start).toHaveBeenCalledWith(expect.anything(), "outlook", {
      email: "a@b.com",
      mailbox_label: "work",
      import_scope: "custom",
      import_since: "2026-01-01",
      import_before: "2026-06-01",
    });
  });

  it("polls pending then connected and calls onConnected with the source id", async () => {
    const start = vi.fn().mockResolvedValue(FLOW);
    const poll = vi
      .fn()
      .mockResolvedValueOnce({ status: "pending" })
      .mockResolvedValueOnce({ status: "connected", source_id: "src-9", verified: true });
    const { onConnected } = renderPanel({ start, poll });

    await screen.findByLabelText(/device code/i);
    await vi.advanceTimersByTimeAsync(1000); // first poll → pending → reschedule
    await vi.advanceTimersByTimeAsync(1000); // second poll → connected

    await waitFor(() => expect(onConnected).toHaveBeenCalledWith("src-9", true));
    expect(await screen.findByText(/is connected/i)).toHaveTextContent(/verified/i);
    expect(poll).toHaveBeenCalledWith(expect.anything(), "outlook", "F1");
  });

  it("soft-save (verified:false) shows the verify-on-first-sync copy", async () => {
    const start = vi.fn().mockResolvedValue(FLOW);
    const poll = vi.fn().mockResolvedValue({ status: "connected", source_id: "src-1", verified: false });
    const { onConnected } = renderPanel({ start, poll });

    await screen.findByLabelText(/device code/i);
    await vi.advanceTimersByTimeAsync(1000);

    await waitFor(() => expect(onConnected).toHaveBeenCalledWith("src-1", false));
    expect(screen.getByText(/verify it on the first sync/i)).toBeInTheDocument();
  });

  it("treats a 429 poll as 'still waiting', never an error", async () => {
    const start = vi.fn().mockResolvedValue(FLOW);
    const poll = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(429, "rate limited"))
      .mockResolvedValueOnce({ status: "connected", source_id: "src-2", verified: false });
    const { onConnected } = renderPanel({ start, poll });

    await screen.findByLabelText(/device code/i);
    await vi.advanceTimersByTimeAsync(1000); // 429 → keep waiting
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(1000); // connected

    await waitFor(() => expect(onConnected).toHaveBeenCalledWith("src-2", false));
  });

  it("renders a terminal error when start 503s (not configured on this server)", async () => {
    const start = vi.fn().mockRejectedValue(new ApiError(503, "no client_id"));
    const poll = vi.fn();
    renderPanel({ start, poll });

    expect(await screen.findByRole("alert")).toHaveTextContent(/isn't set up on this server/i);
    expect(poll).not.toHaveBeenCalled();
  });

  it("shows the declined message on denied", async () => {
    const start = vi.fn().mockResolvedValue(FLOW);
    const poll = vi.fn().mockResolvedValue({ status: "denied" });
    renderPanel({ start, poll });

    await screen.findByLabelText(/device code/i);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await screen.findByRole("alert")).toHaveTextContent(/declined/i);
  });

  it("shows the expired message on expired", async () => {
    const start = vi.fn().mockResolvedValue(FLOW);
    const poll = vi.fn().mockResolvedValue({ status: "expired" });
    renderPanel({ start, poll });

    await screen.findByLabelText(/device code/i);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await screen.findByRole("alert")).toHaveTextContent(/expired/i);
  });

  it("stops polling after unmount", async () => {
    const start = vi.fn().mockResolvedValue(FLOW);
    const poll = vi.fn().mockResolvedValue({ status: "pending" });
    const onConnected = vi.fn();
    const { unmount } = render(
      <OAuthDevicePanel
        client={dummyClient()}
        provider="outlook"
        providerLabel="Outlook.com"
        email="a@b.com"
        mailboxLabel="work"
        onConnected={onConnected}
        api={{ start, poll }}
      />,
    );
    await screen.findByLabelText(/device code/i);
    unmount();
    const before = poll.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(poll.mock.calls.length).toBe(before); // no polls after unmount
    expect(onConnected).not.toHaveBeenCalled();
  });
});
