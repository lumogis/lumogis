// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-724 — one library over three stores.
//
// The list answers *what do I have*; the origin answers *where do I go to work
// with it*. That split is what makes a single list over several stores honest
// rather than a leaky abstraction: a paperless row's actions are not a Lumogis
// viewer's actions, because paperless owns the artifact.
//
// Two things here are guards against regressions found in review, not new
// behaviour:
//
//   * `humanizeStoredName` sliced every name at its last `/`, which silently
//     truncated a paperless title like "Bills / Electricity 2026". That slicing
//     is only correct when falling back to a PATH.
//   * the share and re-ingest refusals are shown as explanations rather than
//     left to fail on click — the server returns 409/422 either way, but a
//     control that fails when pressed is worse than one that says why first.

import { describe, expect, it } from "vitest";

import {
  EXTERNAL_REINGEST_EXPLANATION,
  EXTERNAL_SHARE_EXPLANATION,
  isExternal,
  originLabel,
  originOf,
  paperlessDocumentId,
} from "../../../src/features/documents/documentOrigin";
import { humanizeStoredName } from "../../../src/util/humanizeStoredName";

const PAPERLESS = "paperless://src-uuid/documents/42";
const ATTACHMENT = "email-attachment://src-uuid/sig-uuid/0";
const UPLOAD = "/data/docs/invoice.pdf";

describe("where a document came from", () => {
  it.each([
    [PAPERLESS, "paperless"],
    [ATTACHMENT, "email"],
    [UPLOAD, "upload"],
    ["", "upload"],
    [null, "upload"],
  ])("%s is %s", (path, expected) => {
    expect(originOf(path as string | null)).toBe(expected);
  });

  it("matches on a PREFIX, so a local file named like a scheme is still local", () => {
    // Mirrors the server's `is_external_path`. Substring matching would refuse
    // to share a genuine uploaded document and badge it wrongly.
    expect(isExternal("/data/weird/paperless://not-really.pdf")).toBe(false);
    expect(isExternal("/data/notes about http://example.com.txt")).toBe(false);
  });

  it("badges external documents and leaves uploads unbadged", () => {
    // Badging every row would make the badge worthless — the upload is the
    // unremarkable case.
    expect(originLabel(PAPERLESS)).toBe("From paperless");
    expect(originLabel(ATTACHMENT)).toBe("From email");
    expect(originLabel(UPLOAD)).toBe("");
  });
});

describe("the display name", () => {
  it("does NOT truncate a stored name containing a slash", () => {
    // The regression: `humanizeStoredName` sliced at the last "/" for every
    // input, so a paperless title with a category prefix lost most of itself.
    expect(humanizeStoredName("Bills / Electricity 2026", PAPERLESS)).toBe(
      "Bills / Electricity 2026",
    );
  });

  it("still takes the basename when falling back to a path", () => {
    // The slicing exists for this case and must survive the fix.
    expect(humanizeStoredName(null, UPLOAD)).toBe("invoice.pdf");
  });

  it("never renders an external document as a bare number", () => {
    // Without a stored name the path's last segment is `42` / `0`. A library of
    // bare integers is not browsable, which is the whole point of LUM-724.
    expect(humanizeStoredName("Electricity bill", PAPERLESS)).not.toMatch(/^\d+$/);
    expect(humanizeStoredName("invoice.pdf", ATTACHMENT)).not.toMatch(/^\d+$/);
  });

  it("falls back to something readable rather than empty", () => {
    expect(humanizeStoredName(null, null)).toBe("Untitled document");
  });
});

describe("actions differ by origin", () => {
  it("explains why an external document cannot be shared", () => {
    // The reason is not guessable — the upstream can delete the original, and a
    // household copy would outlive it.
    expect(EXTERNAL_SHARE_EXPLANATION).toMatch(/paperless|email/i);
    expect(EXTERNAL_SHARE_EXPLANATION.length).toBeGreaterThan(40);
  });

  it("names the upstream system on re-ingest rather than saying 'unavailable'", () => {
    expect(EXTERNAL_REINGEST_EXPLANATION.paperless).toMatch(/paperless/i);
    expect(EXTERNAL_REINGEST_EXPLANATION.email).toMatch(/email|attachment/i);
  });
});

describe("the paperless deep link", () => {
  it("extracts the document id", () => {
    expect(paperlessDocumentId(PAPERLESS)).toBe("42");
  });

  it("is null for anything that is not a paperless document", () => {
    expect(paperlessDocumentId(ATTACHMENT)).toBeNull();
    expect(paperlessDocumentId(UPLOAD)).toBeNull();
  });

  it("refuses a non-numeric trailing segment", () => {
    // The id goes into a URL. Accepting an arbitrary trailing segment from a
    // stored path would let it steer where the user is sent.
    expect(paperlessDocumentId("paperless://s/documents/../../evil")).toBeNull();
    expect(paperlessDocumentId("paperless://s/documents/")).toBeNull();
  });
});
