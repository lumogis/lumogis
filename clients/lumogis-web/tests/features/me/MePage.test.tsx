// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { MePage } from "../../../src/features/me/MePage";

describe("MePage (Phase 2A subshell)", () => {
  it("renders subshell layout and settings nav with profile outlet", () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/me/profile"]}>
        <Routes>
          <Route path="/me" element={<MePage />}>
            <Route path="profile" element={<h1>Profile</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(container.querySelector(".lumogis-subshell.lumogis-subshell--me")).not.toBeNull();
    expect(container.querySelector(".lumogis-subshell__content")).not.toBeNull();
    expect(screen.getByRole("navigation", { name: /^settings$/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Profile" })).toBeInTheDocument();
  });
});
