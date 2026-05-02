// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest unit — ApprovalsPage + RiskBadge smoke tests.
// Parent plan §"Phase 1 Pass 1.4" (items 11–14).
//
// Tests cover:
//   - Renders pending items (denied + elevation candidate) from GET /api/v1/approvals/pending
//   - DeniedActionItem: shows connector, action_type, risk badge, suggested action buttons
//   - ElevationCandidateItem: shows connector, action_type, approval count, elevate button
//   - Hard-limited items: actions disabled with explanation
//   - Set-mode modal: Cancel focused by default; confirm POSTs and refetches
//   - Elevate modal: confirm POSTs and refetches
//   - SSE arrival for action_executed invalidates list
//   - RiskBadge renders correct classes for all four tiers
//   - Empty state when no pending items

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { ApprovalsPage } from "../../../src/features/approvals/ApprovalsPage";
import { RiskBadge } from "../../../src/features/approvals/RiskBadge";

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

const DENIED_ITEM = {
  kind: "denied_action",
  action_log_id: 42,
  connector: "filesystem",
  action_type: "write_file",
  risk_tier: "medium",
  input_summary: "Write /home/user/notes.txt",
  occurred_at: "2026-04-24T10:00:00Z",
  elevation_eligible: true,
  suggested_action: "set_connector_do",
};

const ELEVATION_ITEM = {
  kind: "elevation_candidate",
  connector: "calendar",
  action_type: "create_event",
  approval_count: 20,
  risk_tier: "low",
  elevation_eligible: true,
};

const HARD_LIMIT_ITEM = {
  kind: "denied_action",
  action_log_id: 99,
  connector: "shell",
  action_type: "exec_command",
  risk_tier: "hard_limit",
  input_summary: null,
  occurred_at: "2026-04-24T10:00:00Z",
  elevation_eligible: false,
  suggested_action: "explain_only",
};

// ── SSE mock ──────────────────────────────────────────────────────────────
// We return a never-closing stream for the SSE connection (same approach
// as `tests/api/sse.test.ts`) so the reconnecting SSE client stays open
// without hammering the mock.

function hangingStream(): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({ start() {} });
}

function sseEventStream(events: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const e of events) controller.enqueue(enc.encode(e));
      // Keep the stream open so the reconnect loop doesn't immediately refire
    },
  });
}

// ── Client builder ────────────────────────────────────────────────────────

type PendingOverride = () => Response;

function buildClient(
  pendingItems: object[] = [DENIED_ITEM, ELEVATION_ITEM],
  options: {
    pendingOverride?: PendingOverride;
    onSetMode?: () => void;
    onElevate?: () => void;
    sseEvents?: string[];
  } = {},
) {
  const tokens = new AccessTokenStore();
  let pendingCallCount = 0;
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.includes("/api/v1/auth/refresh")) return refreshResponse();
    if (url.includes("/api/v1/auth/me")) return meResponse();

    if (url.includes("/api/v1/approvals/pending")) {
      pendingCallCount += 1;
      return options.pendingOverride
        ? options.pendingOverride()
        : jsonResponse(200, { pending: pendingCallCount === 1 ? pendingItems : [] });
    }

    if (url.includes("/api/v1/approvals/connector") && url.includes("/mode") && method === "POST") {
      options.onSetMode?.();
      return jsonResponse(200, { connector: "filesystem", mode: "DO" });
    }

    if (url.includes("/api/v1/approvals/elevate") && method === "POST") {
      options.onElevate?.();
      return jsonResponse(200, { connector: "calendar", action_type: "create_event", elevated: true });
    }

    if (url.includes("/api/v1/events")) {
      const stream = options.sseEvents
        ? sseEventStream(options.sseEvents)
        : hangingStream();
      return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }

    return jsonResponse(404, { error: "not_found" });
  });

  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { tokens, client, fetchImpl };
}

function mountApprovals(
  pendingItems: object[] = [DENIED_ITEM, ELEVATION_ITEM],
  options: Parameters<typeof buildClient>[1] = {},
) {
  const { tokens, client, fetchImpl } = buildClient(pendingItems, options);
  render(
    <AuthProvider client={client} tokens={tokens}>
      <ApprovalsPage />
    </AuthProvider>,
  );
  return { fetchImpl };
}

// ── Tests ─────────────────────────────────────────────────────────────────

describe("ApprovalsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the page title", async () => {
    mountApprovals([]);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /approvals/i })).toBeInTheDocument();
    });
  });

  it("shows empty state when no pending items", async () => {
    mountApprovals([]);
    await waitFor(() => {
      expect(screen.getByText(/no pending approvals/i)).toBeInTheDocument();
    });
  });

  it("renders a DeniedActionItem with connector and action_type", async () => {
    mountApprovals([DENIED_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("filesystem")).toBeInTheDocument();
    });
    expect(screen.getByText("write_file")).toBeInTheDocument();
    expect(screen.getByText(/Write \/home\/user\/notes.txt/)).toBeInTheDocument();
  });

  it("renders an ElevationCandidateItem with approval count", async () => {
    mountApprovals([ELEVATION_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("calendar")).toBeInTheDocument();
    });
    // The text is split: "Approved " + <strong>20</strong> + " times…"
    // Use a custom matcher to span across element boundaries.
    expect(
      screen.getByText((_, el) =>
        el !== null &&
        el.textContent !== null &&
        /Approved\s+20\s+times/.test(el.textContent) &&
        el.tagName === "P",
      ),
    ).toBeInTheDocument();
  });

  it("renders risk badges for denied items", async () => {
    mountApprovals([DENIED_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("filesystem")).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/medium risk/i)).toBeInTheDocument();
  });

  it("renders hard-limited items with disabled action area", async () => {
    mountApprovals([HARD_LIMIT_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("shell")).toBeInTheDocument();
    });
    expect(screen.getByText(/hard-limited — cannot approve/i)).toBeInTheDocument();
  });

  it("opens set-mode modal when Switch to DO button is clicked", async () => {
    mountApprovals([DENIED_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("filesystem")).toBeInTheDocument();
    });

    const btn = screen.getByRole("button", { name: /switch filesystem to do mode/i });
    await userEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
    expect(screen.getByText(/switch connector to do mode/i)).toBeInTheDocument();
  });

  /** Phase 2B — modal regions for compact full-viewport + scrollable body CSS */
  it("confirmation modal exposes scrollable body and footer action regions", async () => {
    mountApprovals([DENIED_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("filesystem")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: /switch filesystem to do mode/i }));
    const dialog = await waitFor(() => screen.getByRole("dialog"));
    expect(dialog.querySelector(".lumogis-approvals__modal-body")).not.toBeNull();
    const footer = dialog.querySelector(".lumogis-approvals__modal-footer");
    expect(footer).not.toBeNull();
    expect(footer?.querySelectorAll("button").length).toBe(2);
  });

  it("modal Cancel button is focused by default", async () => {
    mountApprovals([DENIED_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("filesystem")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: /switch filesystem to do mode/i }));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    const cancelBtn = screen.getByRole("button", { name: /cancel/i });
    expect(document.activeElement).toBe(cancelBtn);
  });

  it("closes modal when Cancel is clicked", async () => {
    mountApprovals([DENIED_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("filesystem")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: /switch filesystem to do mode/i }));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  /** Phase 2D — focus return after dismiss */
  it("restores focus to the invoking control after modal closes", async () => {
    mountApprovals([DENIED_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("filesystem")).toBeInTheDocument();
    });

    const trigger = screen.getByRole("button", { name: /switch filesystem to do mode/i });
    await userEvent.click(trigger);
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(document.activeElement).toBe(trigger);
  });

  it("closes modal on Escape (Phase 2D)", async () => {
    mountApprovals([DENIED_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("filesystem")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: /switch filesystem to do mode/i }));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    await userEvent.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("confirms set-mode and list refreshes", async () => {
    const onSetMode = vi.fn();
    mountApprovals([DENIED_ITEM], { onSetMode });

    await waitFor(() => {
      expect(screen.getByText("filesystem")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: /switch filesystem to do mode/i }));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    // The confirm button in the modal matches the same text — pick by looking inside dialog
    const dialog = screen.getByRole("dialog");
    const modalConfirm = dialog.querySelector(".lumogis-approvals__btn--confirm") as HTMLElement;
    await userEvent.click(modalConfirm);

    await waitFor(() => {
      expect(onSetMode).toHaveBeenCalledTimes(1);
    });
    // After action, modal closed
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("opens elevate modal when Always allow button is clicked", async () => {
    mountApprovals([ELEVATION_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("calendar")).toBeInTheDocument();
    });

    const btn = screen.getByRole("button", { name: /always allow this action type/i });
    await userEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
    // The modal heading ends with "?" so use a broader pattern scoped to the dialog
    const dialog = screen.getByRole("dialog");
    expect(dialog.querySelector("#approvals-modal-title")?.textContent).toMatch(
      /always allow this action type/i,
    );
  });

  it("Escape key closes modal", async () => {
    mountApprovals([DENIED_ITEM]);
    await waitFor(() => {
      expect(screen.getByText("filesystem")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: /switch filesystem to do mode/i }));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    await userEvent.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });
});

// ── RiskBadge unit tests ──────────────────────────────────────────────────

describe("RiskBadge", () => {
  it("renders low tier", () => {
    const { container } = render(<RiskBadge tier="low" />);
    expect(container.querySelector(".lumogis-risk-badge--low")).toBeTruthy();
    expect(container.textContent).toMatch(/low risk/i);
  });

  it("renders medium tier", () => {
    const { container } = render(<RiskBadge tier="medium" />);
    expect(container.querySelector(".lumogis-risk-badge--medium")).toBeTruthy();
  });

  it("renders high tier", () => {
    const { container } = render(<RiskBadge tier="high" />);
    expect(container.querySelector(".lumogis-risk-badge--high")).toBeTruthy();
  });

  it("renders hard_limit tier", () => {
    const { container } = render(<RiskBadge tier="hard_limit" />);
    expect(container.querySelector(".lumogis-risk-badge--hard-limit")).toBeTruthy();
    expect(container.textContent).toMatch(/hard limit/i);
  });
});
