// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-713 — the inline image strip.
//
// The property worth pinning here is a *negative* one: this component fetches
// nothing and creates no object URL, so there is no lifecycle to leak. The
// server already identified the bytes; everything downstream just places them.
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EmailBody } from "../../../src/features/email/EmailBody";
import { InlineImageStrip } from "../../../src/features/email/InlineImageStrip";
import type { EmailInlineImage } from "../../../src/api/emailSend";

function image(n: number, mime: EmailInlineImage["mime_type"] = "image/png"): EmailInlineImage {
  return {
    content_id: `<i${n}>`,
    mime_type: mime,
    size_bytes: 128,
    data_uri: `data:${mime};base64,AAAA${n}`,
  };
}

describe("InlineImageStrip", () => {
  it("renders one img per image, straight from the data: URI", () => {
    render(<InlineImageStrip images={[image(1), image(2), image(3)]} />);

    const imgs = screen.getAllByRole("img");
    expect(imgs).toHaveLength(3);
    expect(imgs.map((i) => i.getAttribute("src"))).toEqual([
      "data:image/png;base64,AAAA1",
      "data:image/png;base64,AAAA2",
      "data:image/png;base64,AAAA3",
    ]);
  });

  it("neither fetches nor mints an object URL", () => {
    // The blob-URL design this replaced needed an owner for revoke(). Asserting
    // the absence is what keeps that complexity from creeping back in.
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const urlSpy = vi.spyOn(URL, "createObjectURL");

    render(<InlineImageStrip images={[image(1)]} />);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(urlSpy).not.toHaveBeenCalled();
  });

  it("renders nothing at all when there are no images", () => {
    const { container } = render(<InlineImageStrip images={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("says so when a cap dropped images", () => {
    render(<InlineImageStrip images={[image(1)]} truncated />);
    expect(screen.getByText(/weren’t shown/)).toBeTruthy();
  });

  it("says so even when every image was dropped", () => {
    // The empty-and-truncated case: silently rendering nothing would tell the
    // reader the message had no images, which is the opposite of the truth.
    render(<InlineImageStrip images={[]} truncated />);
    expect(screen.getByText(/weren’t shown/)).toBeTruthy();
  });

  it("gives each image a position in its alt text", () => {
    // A Content-ID is a machine handle, not a description — numbering is the
    // only honest thing a screen reader can be told.
    render(<InlineImageStrip images={[image(1), image(2)]} />);
    expect(screen.getByAltText("Image 1 of 2 from this message")).toBeTruthy();
    expect(screen.getByAltText("Image 2 of 2 from this message")).toBeTruthy();
  });
});

describe("EmailBody with a strip", () => {
  it("places the images below the text", () => {
    render(<EmailBody body="Sports day is Thursday." inlineImages={[image(1)]} />);

    const text = screen.getByText(/Sports day/);
    const strip = screen.getByTestId("inline-image-strip");
    expect(text.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("stops claiming images are not displayed once they are", () => {
    render(<EmailBody body="hi" inlineImages={[image(1)]} />);

    expect(screen.queryByText(/images and formatting are not displayed/)).toBeNull();
    expect(screen.getByText(/formatting is not displayed/)).toBeTruthy();
  });

  it("still states the text-only limitation when there are no images", () => {
    render(<EmailBody body="hi" />);
    expect(screen.getByText(/images and formatting are not displayed/)).toBeTruthy();
  });

  it("still shows the images on an image-only message", () => {
    // A scanned letter or a cid:-only marketing mail has images and NO text,
    // and the server answers `no_body`. The reason branch returned early, so
    // the feature did nothing on exactly the messages it exists for.
    render(<EmailBody body="" reason="no_body" inlineImages={[image(1), image(2)]} />);

    expect(screen.getByText(/No message text available/)).toBeTruthy();
    expect(screen.getAllByRole("img")).toHaveLength(2);
  });

  it("shows no strip when a failure carried no images", () => {
    render(<EmailBody body="" reason="mailbox_unreachable" />);
    expect(screen.queryByTestId("inline-image-strip")).toBeNull();
  });

  it("does not contradict the strip when a cap dropped every image", () => {
    // "Some images weren’t shown" directly above "images are not displayed"
    // reads as two different explanations for one thing.
    render(<EmailBody body="hi" inlineImages={[]} inlineImagesTruncated />);

    expect(screen.getByText(/weren’t shown/)).toBeTruthy();
    expect(screen.queryByText(/images and formatting are not displayed/)).toBeNull();
    expect(screen.getByText(/Showing text only — formatting is not displayed/)).toBeTruthy();
  });
});
