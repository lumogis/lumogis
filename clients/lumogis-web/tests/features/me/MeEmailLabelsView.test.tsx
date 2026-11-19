// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-629 — MeEmailLabelsView: lists labels, adds one from its name, re-sorts,
// asks before turning off or deleting (LUM-993 plan B Pass 4).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeEmailLabelsView } from "../../../src/features/me/MeEmailLabelsView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

const DEFAULTS = {
  categories: [
    { slug: "school", label: "School", enabled: true, is_custom: false, is_protected: false, sort: 0 },
    { slug: "receipts", label: "Receipts", enabled: true, is_custom: true, is_protected: false, sort: 1 },
    { slug: "other", label: "Other", enabled: true, is_custom: false, is_protected: true, sort: 0 },
  ],
};

/** A fake API: records PUT bodies and reclassify calls. */
function fakeApi(rules: unknown[] = []) {
  const taxPuts: unknown[] = [];
  const rulePuts: unknown[] = [];
  const resorted: string[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const u = String(input);
    if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
    if (u.includes("/reclassify")) {
      resorted.push(u);
      return jsonResponse(200, { slug: "school", scanned: 3, moved: 1, failed: 0 });
    }
    if (u.includes("/email-classification-rules")) {
      if (init?.method === "PUT") rulePuts.push(JSON.parse(String(init.body)));
      return jsonResponse(200, { rules });
    }
    if (u.includes("/email-taxonomy")) {
      if (init?.method === "PUT") taxPuts.push(JSON.parse(String(init.body)));
      return jsonResponse(200, DEFAULTS);
    }
    return jsonResponse(404, {});
  }) as unknown as typeof fetch;
  return { fetchImpl, taxPuts, rulePuts, resorted };
}

async function openMenu(name: string) {
  await userEvent.click(await screen.findByRole("button", { name }));
  return screen.findByRole("menu");
}

function renderView(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  return render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <MeEmailLabelsView />
    </AuthProvider>,
  );
}

describe("MeEmailLabelsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("lists labels, marks your own, and keeps Other to Re-sort only", async () => {
    renderView(fakeApi().fetchImpl);
    expect(await screen.findByRole("button", { name: "Actions for School" })).toBeInTheDocument();
    expect(screen.getByText("Your own")).toBeInTheDocument();
    // Guidance copy: rules first, model may suggest one of your own labels (LUM-631).
    expect(screen.getByText(/on-device model may suggest one of your own labels/i)).toBeInTheDocument();
    const menu = await openMenu("Actions for Other");
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Re-sort old mail"]);
  });

  it("adds a label from its name alone, making the internal name", async () => {
    const api = fakeApi();
    renderView(api.fetchImpl);
    await screen.findByRole("button", { name: "Actions for School" });
    fireEvent.change(screen.getByLabelText("Label name"), { target: { value: "School" } });
    fireEvent.click(screen.getByRole("button", { name: "Add label" }));
    // "school" exists, so the new one is school_2.
    await waitFor(() => expect(api.taxPuts).toContainEqual({ categories: [{ slug: "school_2", label: "School" }] }));
  });

  it("renames a label from its menu", async () => {
    const api = fakeApi();
    renderView(api.fetchImpl);
    const menu = await openMenu("Actions for School");
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Rename" }));
    const input = await screen.findByLabelText("Rename School");
    fireEvent.change(input, { target: { value: "Kids' school" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.taxPuts).toContainEqual({ categories: [{ slug: "school", label: "Kids' school" }] }));
  });

  it("asks before turning a label off, and does nothing on Cancel", async () => {
    const api = fakeApi();
    renderView(api.fetchImpl);
    let menu = await openMenu("Actions for School");
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Turn off" }));
    let dialog = await screen.findByRole("alertdialog", { name: "Turn off School?" });
    expect(within(dialog).getByText(/moves to Other/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(api.taxPuts).toEqual([]);

    menu = await openMenu("Actions for School");
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Turn off" }));
    dialog = await screen.findByRole("alertdialog", { name: "Turn off School?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(api.taxPuts).toContainEqual({ categories: [{ slug: "school", enabled: false }] }));
  });

  it("deletes only your own labels, after asking", async () => {
    const api = fakeApi();
    renderView(api.fetchImpl);
    const schoolMenu = await openMenu("Actions for School");
    expect(within(schoolMenu).queryByRole("menuitem", { name: "Delete" })).toBeNull();
    await userEvent.keyboard("{Escape}");
    const menu = await openMenu("Actions for Receipts");
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Receipts?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.taxPuts).toContainEqual({ deletes: ["receipts"] }));
  });

  it("re-sorts old mail for a label", async () => {
    const api = fakeApi();
    renderView(api.fetchImpl);
    const menu = await openMenu("Actions for School");
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Re-sort old mail" }));
    await waitFor(() => expect(api.resorted.some((u) => u.includes("/email-taxonomy/school/reclassify"))).toBe(true));
  });

  it("shows rules as sentences and deletes one after asking", async () => {
    const rule = { rule_id: "r1", priority: 50, match: { subject_contains: ["invoice", "receipt"] }, category: "school", enabled: true };
    const api = fakeApi([rule]);
    renderView(api.fetchImpl);
    const sentence = "subject contains invoice, receipt → School";
    expect(await screen.findByText(sentence)).toBeInTheDocument();
    const menu = await openMenu(`Actions for rule: ${sentence}`);
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Delete rule" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete this rule?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.rulePuts).toContainEqual({ rules: [] }));
  });

  it("adds a message-text rule (LUM-630)", async () => {
    const api = fakeApi();
    renderView(api.fetchImpl);
    await screen.findByRole("button", { name: "Actions for School" });
    fireEvent.change(screen.getByLabelText("Message text contains"), { target: { value: "field trip" } });
    await userEvent.click(screen.getByRole("combobox", { name: "Rule label" }));
    await userEvent.click(await screen.findByRole("option", { name: "School" }));
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    await waitFor(() =>
      expect(api.rulePuts).toContainEqual({
        rules: [expect.objectContaining({ category: "school", match: { body_contains: ["field trip"] } })],
      }),
    );
  });

  it("says so plainly when the labels cannot load", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchImpl = vi.fn(async (input: RequestInfo) =>
      String(input).includes("/auth/me")
        ? jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" })
        : jsonResponse(500, { detail: "boom" }),
    ) as unknown as typeof fetch;
    renderView(fetchImpl);
    expect(await screen.findByText("Lumogis couldn't load your labels.")).toBeInTheDocument();
  });
});
