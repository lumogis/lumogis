// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { describe, expect, it } from "vitest";

import { sourceNames } from "../../../src/features/chat/sourceNames";

describe("sourceNames", () => {
  it("drops Core's file id prefix and the folders", () => {
    expect(
      sourceNames([{ file_path: "/workspace/uploads/u1/793df19f88a64bba9137da1f189206c5_boiler-invoice.pdf" }]),
    ).toEqual(["boiler-invoice.pdf"]);
  });

  it("lists each file once, in ranked order", () => {
    expect(
      sourceNames([
        { file_path: "/w/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_car-policy-2026.pdf" },
        { file_path: "/w/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_home-insurance-2026.pdf" },
        { file_path: "/w/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_car-policy-2026.pdf" },
      ]),
    ).toEqual(["car-policy-2026.pdf", "home-insurance-2026.pdf"]);
  });

  it("keeps names without an id, such as watched-folder files", () => {
    expect(sourceNames([{ file_path: "/data/documents/manuals/oven.pdf" }])).toEqual(["oven.pdf"]);
  });
});
