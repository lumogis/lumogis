// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Admin system status — stack-status + Ollama pull/delete (LUM-423).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminSystemStatusView } from "../../../src/features/admin/AdminSystemStatusView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { kgStatus } from "../../helpers/knowledgeGraphStatus";
import { formatDateTime } from "../../../src/util/formatTimestamp";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("admin1");
});

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
    coverage: [],
    files_missing: null,
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
    settings?: Record<string, unknown>;
  } = {},
): FetchHandler {
  return async (input) => {
    const u = String(input);
    if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
    if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
      return jsonResponse(200, overrides.stack ?? stackPayload());
    }
    if (u.endsWith("/settings")) {
      return jsonResponse(
        200,
        overrides.settings ?? {
          reranker_enabled: false,
          reranker_backend_live: "none",
          reranker_pending_restart: false,
        },
      );
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
    expect(await screen.findByText(/Everything is running/)).toBeTruthy();
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
    await user.click(screen.getByRole("combobox", { name: "Ollama catalog model" }));
    await user.click(await screen.findByRole("option", { name: "Phi 3" }));
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
    await user.click(screen.getByRole("button", { name: "Delete llama3.2:3b" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(deleteBodies.length).toBe(0);
    await user.click(within(dialog).getByRole("button", { name: "Delete model" }));

    await waitFor(() => expect(deleteBodies.length).toBe(1));
    expect(deleteBodies[0]).toEqual({ name: "llama3.2:3b" });
  });

  it("delete cancelled skips POST", async () => {
    let deleteCalled = false;
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
    await user.click(screen.getByRole("button", { name: "Delete llama3.2:3b" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    await new Promise((r) => setTimeout(r, 50));
    expect(deleteCalled).toBe(false);
  });

  it("delete embedding adds warning", async () => {
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
    await user.click(screen.getByRole("button", { name: "Delete nomic-embed-text" }));

    expect((await screen.findByRole("alertdialog")).textContent).toMatch(/embedding model/i);
  });

  it("delete default chat adds warning", async () => {
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
    await user.click(screen.getByRole("button", { name: "Delete llama3.2:3b" }));

    expect((await screen.findByRole("alertdialog")).textContent).toMatch(/default chat/i);
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

    expect(await screen.findByText("Backups")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("30.0h old");
  });

  // ── LUM-892: what the backup covers, and files missing from disk ──────────

  function backupFetch(payload: Record<string, unknown>, omitKeys: string[] = []) {
    const body = backupStatusPayload(payload) as Record<string, unknown>;
    for (const k of omitKeys) delete body[k];
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) return jsonResponse(200, stackPayload());
      if (u.includes("/api/v1/admin/diagnostics/backup-status")) return jsonResponse(200, body);
      if (u.includes("/api/v1/admin/ollama/discovery")) return jsonResponse(200, discoveryFixture());
      return jsonResponse(404, { detail: "not found" });
    }, { defaultActiveJob: false });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);
  }

  it("lists what the backup covers and what it does not, with notes", async () => {
    backupFetch({
      coverage: [
        { id: "database", label: "Database", covered: true, note: null },
        { id: "uploads", label: "Uploaded documents", covered: true, note: null },
        {
          id: "env_file",
          label: "The .env settings file",
          covered: false,
          note: "holds the key that decrypts saved passwords; keep a copy somewhere safe",
        },
      ],
      stores: [
        { id: "postgres", present: true, skipped: false, skip_reason: null },
        { id: "files", present: true, skipped: false, skip_reason: null, file_count: 12, bytes: 99 },
      ],
    });

    expect(await screen.findByRole("heading", { name: "Covered", level: 3 })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Not covered", level: 3 })).toBeTruthy();
    expect(screen.getByText("Uploaded documents")).toBeTruthy();
    expect(
      screen.getByText(
        "The .env settings file — holds the key that decrypts saved passwords; keep a copy somewhere safe",
      ),
    ).toBeTruthy();
    expect(screen.getByText(/files \(12 files\)/)).toBeTruthy();
  });

  it("shows every backup warning, so a stale warning cannot hide missing files", async () => {
    backupFetch({
      stale: true,
      age_hours: 30,
      warnings: [
        { code: "backup_stale", message: "Last verified backup is 30.0h old (threshold 24h)" },
        {
          code: "backup_files_missing",
          message: "3 uploaded documents or capture files are not on disk (uploads 2, captures 1).",
        },
      ],
    });

    expect(await screen.findByText("Backups")).toBeTruthy();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("30.0h old");
    expect(alert.textContent).toContain("3 uploaded documents or capture files are not on disk");
  });

  it("renders no coverage lists when coverage is empty", async () => {
    backupFetch({ coverage: [] });
    expect(await screen.findByText("Backups")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Covered", level: 3 })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Not covered", level: 3 })).toBeNull();
  });

  it("does not crash when an older Core omits coverage and files_missing entirely", async () => {
    backupFetch({}, ["coverage", "files_missing"]);
    expect(await screen.findByText("Backups")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Covered", level: 3 })).toBeNull();
    expect(screen.getByText(/Stores:/)).toBeTruthy();
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
    expect(screen.getByText(/Last checked: 30 Jun 2026, /)).toBeTruthy();
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
      seedSignedInDevice("admin1");
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
    const updateSection = screen.getByRole("heading", { name: "Software updates" }).closest("[data-slot=card]");
    expect(updateSection?.textContent).toContain(compareError);
    expect(updateSection?.textContent).toContain("vNext");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows reranker active chip when live backend is bge and not pending", async () => {
    const fetchImpl = makeFetchImpl(
      standardAdminHandler({
        settings: {
          reranker_enabled: true,
          reranker_backend_live: "bge",
          reranker_pending_restart: false,
        },
      }),
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByTestId("reranker-active-chip")).toBeTruthy();
    expect(screen.getByTestId("reranker-active-chip").textContent).toMatch(/Reranker active/i);
  });

  it("hides reranker chip when desired on but live backend is still off", async () => {
    const fetchImpl = makeFetchImpl(
      standardAdminHandler({
        settings: {
          reranker_enabled: true,
          reranker_backend_live: "none",
          reranker_pending_restart: true,
        },
      }),
    );
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    await screen.findByText(/Everything is running/);
    expect(screen.queryByTestId("reranker-active-chip")).toBeNull();
  });

  // LUM-989 — the knowledge-graph offer line sits in System status, once, for admins.
  it("shows the knowledge-graph offer line when the server says Turn on is available", async () => {
    const base = standardAdminHandler();
    const fetchImpl = makeFetchImpl(async (input, init) => {
      if (String(input).includes("/api/v1/admin/knowledge-graph")) {
        return jsonResponse(200, kgStatus("off", { install: "release" }));
      }
      return base(input, init);
    });
    globalThis.fetch = fetchImpl;
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <MemoryRouter>
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <AdminSystemStatusView />
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("graph-offer-line")).toBeTruthy();
    expect(screen.getAllByTestId("graph-offer-line")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Open Knowledge graph" }).getAttribute("href")).toBe(
      "/settings/knowledge-graph",
    );
  });

  it("shows no offer line on a checkout", async () => {
    const base = standardAdminHandler();
    const fetchImpl = makeFetchImpl(async (input, init) => {
      if (String(input).includes("/api/v1/admin/knowledge-graph")) {
        return jsonResponse(200, kgStatus("off", { install: "checkout" }));
      }
      return base(input, init);
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);
    await screen.findByText(/Everything is running/);
    expect(screen.queryByTestId("graph-offer-line")).toBeNull();
  });

  // ── LUM-993 Pass 6: plain words first, developer rows in Details ──────────

  it("summarises in one plain line, naming the services that need attention", async () => {
    const stack = stackPayload();
    stack.meta.overall_status = "degraded" as never;
    stack.services[0].state = "down" as never;
    const fetchImpl = makeFetchImpl(standardAdminHandler({ stack }));
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByText(/some parts need attention: Postgres\./)).toBeTruthy();
    expect(screen.getByText("Not running")).toBeTruthy();
  });

  it("shows each service as a row with a status badge, no raw state or runtime kind in the main view", async () => {
    const fetchImpl = makeFetchImpl(standardAdminHandler());
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByText("Running")).toBeTruthy();
    expect(screen.queryByText("healthy")).toBeNull();
    expect(screen.queryByText("docker_compose")).toBeNull();
  });

  it("keeps shell commands and raw detail in the collapsed Details, not the main view", async () => {
    backupFetch({});
    const trigger = await screen.findByRole("button", { name: "Details" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    const make = screen.getByText(/make backup/);
    // Mounted but inside the collapsed region.
    expect(make.closest("[data-slot=collapsible-content]")).toBeTruthy();
    // The main view says the last backup in a plain date.
    expect(await screen.findByText("Last backup")).toBeTruthy();
    expect(screen.getByText(formatDateTime("2026-01-02T03:00:00Z"))).toBeTruthy();
  });

  it("shows an error state with a retry when the status cannot load", async () => {
    const fetchImpl = makeFetchImpl(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      return jsonResponse(500, { detail: "boom" });
    });
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);

    expect(await screen.findByRole("button", { name: /try again/i })).toBeTruthy();
  });
});
