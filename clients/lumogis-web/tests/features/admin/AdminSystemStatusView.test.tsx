// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Admin system status — stack-status + Ollama pull/delete (LUM-423).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminSystemStatusView } from "../../../src/features/admin/AdminSystemStatusView";
import { jsonResponse } from "../../helpers/jsonResponse";

const adminUser = { id: "admin1", email: "a@home.lan", role: "admin" as const };

function discoveryFixture(overrides: Record<string, unknown> = {}) {
  return {
    local: [],
    catalog: [],
    alias_map: { "llama3.2:3b": "llama" },
    embedding_model: "nomic-embed-text",
    default_model: "llama",
    ...overrides,
  };
}

function stackPayload(overrides: { ollama?: { name: string; size_bytes: number; modified_at: null; loaded: boolean }[] } = {}) {
  return {
    meta: {
      generated_at: "2026-06-01T12:00:00Z",
      cache_age_sec: 5,
      stack_control_reachable: true,
      overall_status: "ok" as const,
    },
    services: [
      {
        id: "postgres",
        display_name: "Postgres",
        state: "healthy" as const,
        runtime_kind: "docker_compose" as const,
        runtime_detail: { compose_state: "running" },
        message: null,
      },
    ],
    storage: [
      {
        mount_id: "host_root",
        path_label: "Host root partition",
        partition_id: "1",
        used_bytes: 50,
        total_bytes: 100,
        used_percent: 50,
        warn_threshold_percent: 80,
        status: "ok" as const,
      },
    ],
    ollama: overrides.ollama ?? [{ name: "llama3", size_bytes: 1000, modified_at: null, loaded: true }],
    warnings: [],
  };
}

function backupStatusPayload(overrides: Record<string, unknown> = {}) {
  return {
    enabled: true,
    backup_dir: "/workspace/backups",
    last_snapshot_id: "20260102-030000",
    last_success_at: "2026-01-02T03:00:00Z",
    age_hours: 2,
    stale: false,
    stale_threshold_hours: 24,
    total_bytes: 4096,
    stores: [
      { id: "postgres", present: true, skipped: false, skip_reason: null },
      { id: "qdrant", present: true, skipped: false, skip_reason: null },
      { id: "falkordb", present: false, skipped: true, skip_reason: "graph disabled" },
    ],
    last_verify_status: "ok",
    warnings: [],
    ...overrides,
  };
}

function updateStatusPayload(overrides: Record<string, unknown> = {}) {
  return {
    current_version: "0.8.0",
    latest_version: "0.8.0",
    update_available: false,
    checked: true,
    checked_at: "2026-06-30T12:00:00Z",
    release_url: null as string | null,
    error: null as string | null,
    ...overrides,
  };
}

type FetchHandler = (input: RequestInfo, init?: RequestInit) => Promise<Response>;

function makeFetchImpl(
  handler: FetchHandler,
  options?: { defaultActiveJob?: boolean; defaultUpdateStatus?: boolean },
) {
  const defaultActive = options?.defaultActiveJob ?? true;
  const defaultUpdateStatus = options?.defaultUpdateStatus ?? true;
  const wrapped: FetchHandler = async (input, init) => {
    const u = String(input);
    if (defaultActive && u.includes("/api/v1/admin/ollama/pull/jobs/active")) {
      return jsonResponse(200, { job: null });
    }
    if (defaultActive && u.includes("/api/v1/admin/diagnostics/backup-status")) {
      return jsonResponse(200, backupStatusPayload());
    }
    const response = await handler(input, init);
    if (
      defaultUpdateStatus &&
      u.includes("/api/v1/admin/diagnostics/update-status") &&
      response.status === 404
    ) {
      return jsonResponse(200, updateStatusPayload());
    }
    return response;
  };
  return vi.fn(wrapped) as unknown as typeof fetch;
}

const JOB_ID = "550e8400-e29b-41d4-a716-446655440000";

function jobFixture(
  status: "pending" | "running" | "succeeded" | "failed",
  overrides: Record<string, unknown> = {},
) {
  return {
    job_id: JOB_ID,
    model_name: "tinyllama",
    status,
    progress_pct: status === "running" ? 50 : status === "succeeded" ? 100 : null,
    status_message: status === "running" ? "downloading" : null,
    error_message: status === "failed" ? "pull failed" : null,
    qdrant_init_warning: null as string | null,
    created_at: "2026-06-08T12:00:00Z",
    started_at: "2026-06-08T12:00:01Z",
    finished_at: status === "succeeded" || status === "failed" ? "2026-06-08T12:05:00Z" : null,
    ...overrides,
  };
}

function withAsyncPullMocks(
  handler: FetchHandler,
  opts: {
    pollSequence?: ReturnType<typeof jobFixture>[];
    activeJob?: ReturnType<typeof jobFixture> | null;
    onAsyncStart?: (body: unknown) => void;
  } = {},
) {
  const pollSequence = opts.pollSequence ?? [jobFixture("running"), jobFixture("succeeded")];
  let pollIndex = 0;
  return makeFetchImpl(async (input, init) => {
    const u = String(input);
    if (u.includes("/api/v1/admin/ollama/pull/jobs/active")) {
      return jsonResponse(200, { job: opts.activeJob ?? null });
    }
    if (u.includes("/api/v1/admin/ollama/pull/async")) {
      opts.onAsyncStart?.(JSON.parse(String(init?.body)));
      return jsonResponse(202, { status: "started", job_id: JOB_ID });
    }
    if (u.includes("/api/v1/admin/ollama/pull/jobs/")) {
      const job = pollSequence[Math.min(pollIndex, pollSequence.length - 1)];
      pollIndex += 1;
      return jsonResponse(200, job);
    }
    return handler(input, init);
  }, { defaultActiveJob: false });
}

function renderView(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  const view = render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <AdminSystemStatusView />
    </AuthProvider>,
  );
  return { fetchImpl, ...view };
}

function standardAdminHandler(
  overrides: {
    stack?: ReturnType<typeof stackPayload>;
    discovery?: ReturnType<typeof discoveryFixture>;
    updateStatus?: ReturnType<typeof updateStatusPayload>;
  } = {},
): FetchHandler {
  return async (input) => {
    const u = String(input);
    if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
    if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
      return jsonResponse(200, overrides.stack ?? stackPayload());
    }
    if (u.includes("/api/v1/admin/ollama/discovery")) {
      return jsonResponse(200, overrides.discovery ?? discoveryFixture());
    }
    if (u.includes("/api/v1/admin/diagnostics/update-status")) {
      return jsonResponse(200, overrides.updateStatus ?? updateStatusPayload());
    }
    return jsonResponse(404, { detail: "not found" });
  };
}

describe("AdminSystemStatusView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("calls stack-status and renders services, storage, and ollama", async () => {
    let stackUrl: string | null = null;
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        stackUrl = u;
        return jsonResponse(200, stackPayload());
      }
      if (u.includes("/api/v1/admin/ollama/discovery")) {
        return jsonResponse(200, discoveryFixture());
      }
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;

    renderView(fetchImpl);

    await waitFor(() => expect(stackUrl).toContain("/api/v1/admin/diagnostics/stack-status"));
    expect(await screen.findByText("System status")).toBeTruthy();
    expect(screen.getByText("Postgres")).toBeTruthy();
    expect(screen.getByText("Host root partition")).toBeTruthy();
    expect(screen.getByText("llama3")).toBeTruthy();
  });

  it("renders registry alias badge", async () => {
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        return jsonResponse(
          200,
          stackPayload({
            ollama: [{ name: "llama3.2:3b", size_bytes: 1000, modified_at: null, loaded: true }],
          }),
        );
      }
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByText("llama")).toBeTruthy();
  });

  it("renders embedding badge", async () => {
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        return jsonResponse(
          200,
          stackPayload({
            ollama: [{ name: "nomic-embed-text", size_bytes: 500, modified_at: null, loaded: false }],
          }),
        );
      }
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByText("embedding")).toBeTruthy();
  });

  it("pull calls POST /api/v1/admin/ollama/pull/async", async () => {
    const pullBodies: unknown[] = [];
    const fetchImpl = withAsyncPullMocks(
      async (input) => {
        const u = String(input);
        if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
        if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
        if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
        return jsonResponse(404, { detail: "not found" });
      },
      { onAsyncStart: (body) => pullBodies.push(body) },
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByLabelText("Ollama model name to pull");
    await user.type(screen.getByLabelText("Ollama model name to pull"), "tinyllama");
    await user.click(screen.getByRole("button", { name: "Pull" }));

    await waitFor(() => expect(pullBodies.length).toBe(1));
    expect(pullBodies[0]).toEqual({ name: "tinyllama" });
  });

  it("pull uses catalog when input empty", async () => {
    const pullBodies: unknown[] = [];
    const fetchImpl = withAsyncPullMocks(
      async (input) => {
        const u = String(input);
        if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
        if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
        if (u.includes("/api/v1/admin/ollama/discovery")) {
          return jsonResponse(
            200,
            discoveryFixture({
              catalog: [
                {
                  name: "phi3",
                  installed: false,
                  display_name: "Phi 3",
                },
              ],
            }),
          );
        }
        return jsonResponse(404, { detail: "not found" });
      },
      {
        onAsyncStart: (body) => pullBodies.push(body),
        pollSequence: [jobFixture("running", { model_name: "phi3" }), jobFixture("succeeded", { model_name: "phi3" })],
      },
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByLabelText("Ollama catalog model");
    await user.selectOptions(screen.getByLabelText("Ollama catalog model"), "phi3");
    await user.click(screen.getByRole("button", { name: "Pull" }));

    await waitFor(() => expect(pullBodies.length).toBe(1));
    expect(pullBodies[0]).toEqual({ name: "phi3" });
  });

  it("shows progress bar while pull is running", async () => {
    const fetchImpl = withAsyncPullMocks(
      async (input) => {
        const u = String(input);
        if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
        if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
        if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
        return jsonResponse(404, { detail: "not found" });
      },
      { pollSequence: [jobFixture("running"), jobFixture("running"), jobFixture("succeeded")] },
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByLabelText("Ollama model name to pull");
    await user.type(screen.getByLabelText("Ollama model name to pull"), "tinyllama");
    await user.click(screen.getByRole("button", { name: "Pull" }));

    expect(await screen.findByLabelText("Ollama pull progress")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Pull" })).toBeDisabled();
  });

  it("embedding pull warning visible", async () => {
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByLabelText("Ollama model name to pull");
    await user.type(screen.getByLabelText("Ollama model name to pull"), "nomic-embed-text");

    expect(await screen.findByText(/Qdrant collections/i)).toBeTruthy();
  });

  it("delete confirms then POST", async () => {
    const deleteBodies: unknown[] = [];
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchImpl = makeFetchImpl(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        return jsonResponse(
          200,
          stackPayload({
            ollama: [{ name: "llama3.2:3b", size_bytes: 1000, modified_at: null, loaded: true }],
          }),
        );
      }
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      if (u.includes("/api/v1/admin/ollama/delete")) {
        deleteBodies.push(JSON.parse(String(init?.body)));
        return jsonResponse(200, { status: "deleted", name: "llama3.2:3b" });
      }
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByText("llama3.2:3b");
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteBodies.length).toBe(1));
    expect(deleteBodies[0]).toEqual({ name: "llama3.2:3b" });
  });

  it("delete cancelled skips POST", async () => {
    let deleteCalled = false;
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchImpl = makeFetchImpl(async (input, _init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        return jsonResponse(
          200,
          stackPayload({
            ollama: [{ name: "llama3.2:3b", size_bytes: 1000, modified_at: null, loaded: true }],
          }),
        );
      }
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      if (u.includes("/api/v1/admin/ollama/delete")) {
        deleteCalled = true;
        return jsonResponse(200, { status: "deleted", name: "llama3.2:3b" });
      }
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByText("llama3.2:3b");
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await new Promise((r) => setTimeout(r, 50));
    expect(deleteCalled).toBe(false);
  });

  it("delete embedding adds warning", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        return jsonResponse(
          200,
          stackPayload({
            ollama: [{ name: "nomic-embed-text", size_bytes: 500, modified_at: null, loaded: false }],
          }),
        );
      }
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByText("nomic-embed-text");
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(confirmSpy).toHaveBeenCalled();
    expect(String(confirmSpy.mock.calls[0]?.[0])).toMatch(/embedding model/i);
  });

  it("delete default chat adds warning", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        return jsonResponse(
          200,
          stackPayload({
            ollama: [{ name: "llama3.2:3b", size_bytes: 1000, modified_at: null, loaded: true }],
          }),
        );
      }
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByText("llama3.2:3b");
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(confirmSpy).toHaveBeenCalled();
    expect(String(confirmSpy.mock.calls[0]?.[0])).toMatch(/default chat/i);
  });

  it("pull error surfaces detail", async () => {
    const fetchImpl = makeFetchImpl(async (input, _init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      if (u.includes("/api/v1/admin/ollama/pull/async")) {
        return jsonResponse(502, { detail: "Ollama pull failed: timeout" });
      }
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByLabelText("Ollama model name to pull");
    await user.type(screen.getByLabelText("Ollama model name to pull"), "tinyllama");
    await user.click(screen.getByRole("button", { name: "Pull" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/timeout/i);
  });

  it("invalidates stack-status after pull", async () => {
    let stackStatusCalls = 0;
    const fetchImpl = withAsyncPullMocks(
      async (input) => {
        const u = String(input);
        if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
        if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
          stackStatusCalls += 1;
          return jsonResponse(200, stackPayload());
        }
        if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
        return jsonResponse(404, { detail: "not found" });
      },
      { pollSequence: [jobFixture("succeeded")] },
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    await waitFor(() => expect(stackStatusCalls).toBeGreaterThanOrEqual(1));
    const before = stackStatusCalls;

    const user = userEvent.setup();
    await screen.findByLabelText("Ollama model name to pull");
    await user.type(screen.getByLabelText("Ollama model name to pull"), "tinyllama");
    await user.click(screen.getByRole("button", { name: "Pull" }));

    await waitFor(() => expect(stackStatusCalls).toBeGreaterThan(before), { timeout: 3000 });
  });

  it("shows qdrant init warning when pull returns qdrant_init_warning", async () => {
    const warning =
      "Qdrant collections could not be initialized after pull. Restart the orchestrator to retry.";
    const fetchImpl = withAsyncPullMocks(
      async (input) => {
        const u = String(input);
        if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
        if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
        if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
        return jsonResponse(404, { detail: "not found" });
      },
      {
        pollSequence: [
          jobFixture("succeeded", {
            model_name: "nomic-embed-text",
            qdrant_init_warning: warning,
          }),
        ],
      },
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByLabelText("Ollama model name to pull");
    await user.type(screen.getByLabelText("Ollama model name to pull"), "nomic-embed-text");
    await user.click(screen.getByRole("button", { name: "Pull" }));

    const status = await screen.findByRole("status", {}, { timeout: 3000 });
    expect(status.textContent).toBe(warning);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("surfaces 409 when pull already in progress", async () => {
    const fetchImpl = makeFetchImpl(async (input, _init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      if (u.includes("/api/v1/admin/ollama/pull/async")) {
        return jsonResponse(409, { detail: "ollama pull already in progress" });
      }
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const user = userEvent.setup();
    await screen.findByLabelText("Ollama model name to pull");
    await user.type(screen.getByLabelText("Ollama model name to pull"), "tinyllama");
    await user.click(screen.getByRole("button", { name: "Pull" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/already in progress/i);
  });

  it("resumes polling when active job on mount", async () => {
    const fetchImpl = withAsyncPullMocks(
      async (input) => {
        const u = String(input);
        if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
        if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
        if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
        return jsonResponse(404, { detail: "not found" });
      },
      {
        activeJob: jobFixture("running"),
        pollSequence: [jobFixture("running"), jobFixture("succeeded")],
      },
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByLabelText("Ollama pull progress")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Pull" })).toBeDisabled();
  });

  it("discovery failure keeps services visible", async () => {
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
      if (u.includes("/api/v1/admin/ollama/discovery")) {
        return jsonResponse(502, { detail: "discovery down" });
      }
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByText("Postgres")).toBeTruthy();
    const alerts = screen.getAllByRole("alert");
    expect(alerts.some((a) => a.textContent?.includes("discovery down"))).toBe(true);
  });

  it("shows stale DR backup warning when backup-status reports stale", async () => {
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
      if (u.includes("/api/v1/admin/diagnostics/backup-status")) {
        return jsonResponse(
          200,
          backupStatusPayload({
            stale: true,
            age_hours: 30,
            warnings: [{ code: "backup_stale", message: "Last verified backup is 30.0h old (threshold 24h)" }],
          }),
        );
      }
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      return jsonResponse(404, { detail: "not found" });
    }, { defaultActiveJob: false });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByText("Disaster recovery backup")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("30.0h old");
  });

  it("shows update available alert with release link", async () => {
    const fetchImpl = makeFetchImpl(
      standardAdminHandler({
        updateStatus: updateStatusPayload({
          checked: true,
          update_available: true,
          current_version: "0.7.0",
          latest_version: "0.8.0",
          release_url: "https://github.com/lumogis/lumogis/releases/tag/0.8.0",
        }),
      }),
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/0\.7\.0/);
    expect(alert.textContent).toMatch(/0\.8\.0/);
    expect(screen.getByRole("link", { name: "Release notes" }).getAttribute("href")).toBe(
      "https://github.com/lumogis/lumogis/releases/tag/0.8.0",
    );
    expect(screen.getByText(/make update/i)).toBeTruthy();
  });

  it("shows up to date informational state", async () => {
    const fetchImpl = makeFetchImpl(
      standardAdminHandler({
        updateStatus: updateStatusPayload({
          checked: true,
          update_available: false,
          current_version: "0.8.0",
          latest_version: "0.8.0",
          checked_at: "2026-06-30T12:00:00Z",
          error: null,
        }),
      }),
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByText(/latest release/i)).toBeTruthy();
    expect(screen.getByText(/0\.8\.0/)).toBeTruthy();
    expect(screen.getByText(/Last checked: 2026-06-30T12:00:00Z/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("fail-soft when check disabled", async () => {
    const disabledError = "update check disabled (LUMOGIS_UPDATE_CHECK_ENABLED=0)";
    const fetchImpl = makeFetchImpl(
      standardAdminHandler({
        updateStatus: updateStatusPayload({
          checked: false,
          error: disabledError,
          current_version: "0.8.0",
        }),
      }),
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByText(disabledError)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  describe("update dismissal", () => {
    beforeEach(() => {
      localStorage.clear();
    });

    it("dismiss hides alert until newer version", async () => {
      let updatePayload = updateStatusPayload({
        checked: true,
        update_available: true,
        current_version: "0.7.0",
        latest_version: "0.8.0",
        release_url: "https://github.com/lumogis/lumogis/releases/tag/0.8.0",
      });

      const fetchImpl = makeFetchImpl(async (input) => {
        const u = String(input);
        if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
        if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
        if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
        if (u.includes("/api/v1/admin/diagnostics/update-status")) {
          return jsonResponse(200, updatePayload);
        }
        return jsonResponse(404, { detail: "not found" });
      });
      globalThis.fetch = fetchImpl;

      const user = userEvent.setup();
      const { unmount, rerender } = renderView(fetchImpl);

      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toMatch(/0\.8\.0/);

      await user.click(
        screen.getByRole("button", { name: "Dismiss update notification for version 0.8.0" }),
      );
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.getByText("Software updates")).toBeTruthy();

      rerender(
        <AuthProvider
          client={new ApiClient({ tokens: new AccessTokenStore(), fetchImpl })}
          tokens={new AccessTokenStore()}
          skipRefreshOnMount
        >
          <AdminSystemStatusView />
        </AuthProvider>,
      );
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.getByText("Software updates")).toBeTruthy();

      updatePayload = {
        ...updatePayload,
        latest_version: "0.8.1",
        release_url: "https://github.com/lumogis/lumogis/releases/tag/0.8.1",
      };
      unmount();
      renderView(fetchImpl);

      const alertNew = await screen.findByRole("alert");
      expect(alertNew.textContent).toMatch(/0\.8\.1/);

      await user.click(
        screen.getByRole("button", { name: "Dismiss update notification for version 0.8.1" }),
      );
      expect(localStorage.getItem("lumogis.admin.updateDismissedVersion")).toBe("0.8.1");
    });
  });

  it("missing tag_name is informational", async () => {
    const tagError = "latest release has no tag_name";
    const fetchImpl = makeFetchImpl(
      standardAdminHandler({
        updateStatus: updateStatusPayload({
          checked: false,
          error: tagError,
          current_version: "0.8.0",
          checked_at: "2026-06-30T12:00:00Z",
          latest_version: null,
        }),
      }),
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByText(tagError)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("update-status fetch error", async () => {
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      if (u.includes("/api/v1/admin/diagnostics/update-status")) {
        return jsonResponse(500, { detail: "server error" });
      }
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByText("Software update status unavailable.")).toBeTruthy();
  });

  it("version comparison error is informational", async () => {
    const compareError = "could not compare versions (non-PEP440 tag)";
    const fetchImpl = makeFetchImpl(
      standardAdminHandler({
        updateStatus: updateStatusPayload({
          checked: true,
          update_available: false,
          error: compareError,
          latest_version: "vNext",
          current_version: "0.8.0",
        }),
      }),
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    await screen.findByText("Software updates");
    const updateSection = screen.getByText("Software updates").parentElement;
    expect(updateSection?.textContent).toContain(compareError);
    expect(updateSection?.textContent).toContain("vNext");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
