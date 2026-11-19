// SPDX-License-Identifier: AGPL-3.0-only
//
// components/Button is a thin wrapper over the shadcn ui/button (LUM-993). These
// tests pin the legacy-variant mapping, so the 17 callers keep their meaning.

import { render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";

import { Button } from "../../src/components/Button";
import { legacyButtonClasses } from "../../src/components/buttonClasses";
import { buttonVariants } from "../../src/components/ui/button";
import { cn } from "../../src/lib/utils";

describe("Button", () => {
  it.each([
    ["primary", "default"],
    ["secondary", "outline"],
    ["ghost", "ghost"],
    ["danger", "destructive"],
    ["danger-solid", "destructiveSolid"],
  ] as const)("maps legacy %s to ui/button %s", (legacy, ui) => {
    render(<Button variant={legacy}>Go</Button>);
    const btn = screen.getByRole("button", { name: "Go" });
    expect(btn).toHaveAttribute("data-variant", legacy);
    // ui/button runs its classes through cn (tailwind-merge), so compare the same way.
    expect(btn.className).toBe(cn(buttonVariants({ variant: ui, size: "default" })));
  });

  it("maps sizes md and sm", () => {
    expect(legacyButtonClasses("primary", "md")).toContain("h-9");
    expect(legacyButtonClasses("primary", "sm")).toContain("h-8");
    expect(legacyButtonClasses("primary", "md")).toContain("bg-primary");
  });

  it("styles a link as a button without the global link look", () => {
    // Global `a` rules (accent colour, underline on hover) would otherwise show
    // through where a variant sets no text colour of its own.
    for (const v of ["primary", "secondary", "ghost", "danger", "danger-solid"] as const) {
      const cls = legacyButtonClasses(v).split(" ");
      expect(cls).toEqual(expect.arrayContaining(["no-underline", "hover:no-underline"]));
    }
    expect(legacyButtonClasses("secondary").split(" ")).toContain("text-foreground");
    expect(legacyButtonClasses("ghost").split(" ")).toContain("text-foreground");
    // The primary label keeps its own colour.
    expect(legacyButtonClasses("primary").split(" ")).not.toContain("text-foreground");
    expect(legacyButtonClasses("primary").split(" ")).toContain("text-primary-foreground");
  });

  it("is a plain button by default, keeps disabled, and forwards refs", () => {
    const ref = createRef<HTMLButtonElement>();
    render(
      <Button ref={ref} disabled>
        Disabled
      </Button>,
    );
    const btn = screen.getByRole("button", { name: "Disabled" });
    expect(btn).toHaveAttribute("type", "button");
    expect(btn).toBeDisabled();
    expect(ref.current).toBe(btn);
  });

  it("gives every size a 44px minimum on touch screens", () => {
    // In px, not rem: the page's root text size is 15px, so min-h-11 (2.75rem)
    // came out at 41.25px on a phone (found by recoverable_sends.spec, LUM-993 Pass 7).
    for (const size of ["default", "sm", "lg"] as const) {
      expect(buttonVariants({ size })).toContain("pointer-coarse:min-h-[44px]");
    }
  });
});
