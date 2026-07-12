// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  documentMetadataCaption,
  humanizeStoredName,
} from "../../src/util/humanizeStoredName";

describe("humanizeStoredName", () => {
  it("strips hash prefix from stored filename", () => {
    expect(humanizeStoredName("98cc7107cb1049b98ec422746f665368_household-insurance.md")).toBe(
      "household-insurance.md",
    );
  });

  it("strips workspace upload path and hash prefix", () => {
    expect(
      humanizeStoredName(
        "/workspace/uploads/94bbd03172534e7ca282abcc36bc131c/492dcfde905c4e1ca1d25b1be410e85f_household-insurance.md",
      ),
    ).toBe("household-insurance.md");
  });

  it("falls back to basename from file_path", () => {
    expect(humanizeStoredName(null, "/library/notes.txt")).toBe("notes.txt");
  });
});

describe("documentMetadataCaption", () => {
  it("combines id and path", () => {
    expect(documentMetadataCaption(5, "hash_file.md", "/uploads/u/hash_file.md")).toContain("#5");
    expect(documentMetadataCaption(5, "hash_file.md", "/uploads/u/hash_file.md")).toContain("/uploads/");
  });
});
