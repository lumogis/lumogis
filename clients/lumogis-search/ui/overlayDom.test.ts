// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

/**
 * DOM-level overlay UI tests (LUM-405 / LUM-398 verify-plan gap).
 * @vitest-environment happy-dom
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { createHitRow, type TauriInvoke } from "./hitRow";
import { needsOnboarding, onboardingMarkup } from "./overlayUi";
import type { MemorySearchHit } from "./searchClient";

function mountOnboardingShell(): HTMLDivElement {
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  root.innerHTML = onboardingMarkup({
    wizardBaseUrl: "https://household.example",
    healthStatus: "idle",
    healthMessage: "",
    authMode: "unknown",
    sessionPresent: false,
    loginError: "",
  });
  return root;
}

function mountMainSearchShell(searchDisabled: boolean): HTMLDivElement {
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  root.innerHTML = `
    <div class="toolbar">
      <input type="search" id="q" placeholder="Search memory…" autocomplete="off" ${searchDisabled ? "disabled" : ""} />
      <button type="button" id="btn-settings">Settings</button>
    </div>
  `;
  return root;
}

const sampleHit: MemorySearchHit = {
  id: "/home/user/docs/note.md",
  score: 0.91,
  title: "Note",
  snippet: "snippet text",
  scope: "personal",
};

describe("onboarding_blocks_search_until_complete", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("shows onboarding panel and omits #q when onboardingComplete is false", () => {
    expect(needsOnboarding({ onboardingComplete: false, libraryRoots: [] })).toBe(true);
    const root = mountOnboardingShell();
    expect(root.querySelector("#onboarding")).not.toBeNull();
    expect(root.querySelector("#q")).toBeNull();
  });

  it("includes #q in main shell only after onboarding would complete", () => {
    expect(needsOnboarding({ onboardingComplete: true, libraryRoots: [] })).toBe(false);
    const root = mountMainSearchShell(false);
    const q = root.querySelector<HTMLInputElement>("#q");
    expect(q).not.toBeNull();
    expect(q?.disabled).toBe(false);
  });
});

describe("open_blocked_without_roots", () => {
  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not invoke open_if_allowed when libraryRoots is empty", async () => {
    const invokeFn = vi.fn(async () => {}) as unknown as TauriInvoke;
    const row = createHitRow(sampleHit, [], invokeFn);
    document.body.appendChild(row);
    row.click();
    await vi.waitFor(() => {
      expect(invokeFn).not.toHaveBeenCalled();
    });
    expect(alert).toHaveBeenCalledWith(
      "Set library roots in Settings to open this file locally.",
    );
  });

  it("invokes open_if_allowed when libraryRoots is non-empty", async () => {
    const invokeFn = vi.fn(async () => {}) as unknown as TauriInvoke;
    const row = createHitRow(sampleHit, ["/home/user/library"], invokeFn);
    document.body.appendChild(row);
    row.click();
    await vi.waitFor(() => {
      expect(invokeFn).toHaveBeenCalledWith("open_if_allowed", {
        path: sampleHit.id,
      });
    });
  });
});
