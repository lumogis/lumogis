// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-639 — the client for PATCH /sources/{id}.
// LUM-837 widened it from `updateSourceMode(client, id, fullIngest)` into the
// general `updateSource(client, id, patch)`; the two shipped cases below still
// assert the same things (body shape, path encoding), and the widened cases sit
// alongside them rather than in a second file.
import { describe, expect, it, vi } from "vitest";

import { updateSource } from "../../src/api/emailSources";
import type { ApiClient } from "../../src/api/client";

describe("updateSource", () => {
  it("PATCHes /sources/{id} with the full_ingest body and returns the response", async () => {
    const patchJson = vi.fn().mockResolvedValue({
      status: "updated",
      source_id: "src-1",
      source_type: "imap",
      extraction_method: "imap_full_ingest",
      full_ingest_active: false,
      rescheduled: true,
    });
    const client = { patchJson } as unknown as ApiClient;

    const res = await updateSource(client, "src-1", { full_ingest: true });

    expect(patchJson).toHaveBeenCalledWith("/sources/src-1", { full_ingest: true });
    expect(res.extraction_method).toBe("imap_full_ingest");
    expect(res.full_ingest_active).toBe(false);
  });

  it("encodes the source id in the path", async () => {
    const patchJson = vi.fn().mockResolvedValue({});
    const client = { patchJson } as unknown as ApiClient;

    await updateSource(client, "a/b id", { full_ingest: false });

    expect(patchJson).toHaveBeenCalledWith("/sources/a%2Fb%20id", { full_ingest: false });
  });

  it("sends only the fields supplied, so an omitted field is never overwritten", async () => {
    const patchJson = vi.fn().mockResolvedValue({});
    const client = { patchJson } as unknown as ApiClient;

    await updateSource(client, "src-1", { name: "Archive" });

    expect(patchJson).toHaveBeenCalledWith("/sources/src-1", { name: "Archive" });
  });

  it("carries the general fields for a non-IMAP source", async () => {
    const patchJson = vi.fn().mockResolvedValue({
      status: "updated",
      source_id: "src-2",
      source_type: "paperless",
      extraction_method: "paperless_http",
      // null, not false: the request never mentioned mode.
      full_ingest_active: null,
      rescheduled: true,
      name: "Household archive",
      poll_interval: 900,
    });
    const client = { patchJson } as unknown as ApiClient;

    const res = await updateSource(client, "src-2", {
      name: "Household archive",
      poll_interval: 900,
    });

    expect(patchJson).toHaveBeenCalledWith("/sources/src-2", {
      name: "Household archive",
      poll_interval: 900,
    });
    expect(res.full_ingest_active).toBeNull();
    expect(res.name).toBe("Household archive");
  });
});
