// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// AnswerText (LUM-993 follow-up): Lumogis's Markdown answers shown formatted,
// never as HTML, links only for http(s).

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AnswerText } from "../../../src/features/chat/AnswerText";

describe("AnswerText", () => {
  it("formats bold, italics and inline code instead of showing the markers", () => {
    const { container } = render(<AnswerText text="He quoted **2,400** for the *garage* roof, see `quote.pdf`." />);
    expect(container.textContent).toBe("He quoted 2,400 for the garage roof, see quote.pdf.");
    expect(container.querySelector("strong")).toHaveTextContent("2,400");
    expect(container.querySelector("em")).toHaveTextContent("garage");
    expect(container.querySelector("code")).toHaveTextContent("quote.pdf");
  });

  it("makes paragraphs, bullet and numbered lists, and code blocks", () => {
    const text = [
      "Three notes:",
      "",
      "1. A session note.",
      "2. A roofer quote,",
      "   with no amount.",
      "",
      "- one",
      "- two",
      "",
      "```",
      "let x = 1;",
      "```",
    ].join("\n");
    const { container } = render(<AnswerText text={text} />);
    const ol = container.querySelector("ol")!;
    expect(within(ol).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "A session note.",
      "A roofer quote, with no amount.",
    ]);
    expect(within(container.querySelector("ul")!).getAllByRole("listitem")).toHaveLength(2);
    expect(container.querySelector("pre")).toHaveTextContent("let x = 1;");
    expect(screen.getByText("Three notes:").closest("p")).not.toBeNull();
  });

  it("keeps headings in an answer as bold lines, not page headings", () => {
    render(<AnswerText text={"## Summary\nAll good."} />);
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.getByText("Summary")).toBeInTheDocument();
  });

  it("opens http(s) links in a new tab and drops any other link target", () => {
    render(<AnswerText text="[docs](https://example.org/a) and [bad](javascript:alert(1)) and [data](data:text/html,x)" />);
    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("href", "https://example.org/a");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText("bad").closest("a")).toBeNull();
  });

  it("never turns text into HTML", () => {
    const { container } = render(<AnswerText text={'<img src=x onerror="alert(1)"> **<b>hi</b>**'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
  });

  it("shows unfinished syntax while an answer streams in as plain text", () => {
    const { container } = render(<AnswerText text={"It costs **2,4"} />);
    expect(container.textContent).toBe("It costs **2,4");
    const fence = render(<AnswerText text={"```\nlet x"} />);
    expect(fence.container.querySelector("pre")).toHaveTextContent("let x");
  });
});
