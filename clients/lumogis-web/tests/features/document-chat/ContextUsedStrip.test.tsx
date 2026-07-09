// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ContextUsedStrip } from "../../../src/features/document-chat/ContextUsedStrip";

describe("ContextUsedStrip", () => {
  it("renders citation chunk indices from props", () => {
    render(
      <ContextUsedStrip
        citations={[
          { chunk_index: 4, file_path: "/a.pdf", score: 0.8, score_kind: "rerank" },
          { chunk_index: 7, file_path: "/a.pdf", score: 0.7, score_kind: "rerank" },
          { chunk_index: 12, file_path: "/a.pdf", score: 0.6, score_kind: "rerank" },
        ]}
      />,
    );
    expect(screen.getByTestId("context-used-strip")).toHaveTextContent("Context used: chunks 4, 7, 12");
  });

  it("returns null when citations are empty", () => {
    const { container } = render(<ContextUsedStrip citations={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
