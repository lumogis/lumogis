// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The legacy Button variants and sizes, and the classes for a link or other
// element that must look like a Button (LUM-993). Its own file so Button.tsx
// exports only the component (React Fast Refresh).
//
// Legacy variant → ui/button variant, written out on purpose: the legacy
// "secondary" is an outlined button, not ui/button's filled "secondary".

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "danger-solid";
export type ButtonSize = "md" | "sm";

export const VARIANT = {
  primary: "default",
  secondary: "outline",
  ghost: "ghost",
  danger: "destructive",
  "danger-solid": "destructiveSolid",
} as const;

export const SIZE = { md: "default", sm: "sm" } as const;

/** Classes for a link or element that must look like a Button. */
export function legacyButtonClasses(
  variant: ButtonVariant = "secondary",
  size: ButtonSize = "md",
  extra?: string,
): string {
  // ui/button runs its classes through cn (tailwind-merge); do the same, so a
  // link never carries two clashing classes (border-transparent and border-border).
  // On an <a>, the global link rules (accent colour, underline and dimming on
  // hover) would show through where a variant sets no text colour of its own.
  // Literals stay inside cn() so the class-name guard scans them.
  return cn(
    buttonVariants({ variant: VARIANT[variant], size: SIZE[size] }),
    "no-underline hover:no-underline hover:filter-none",
    (variant === "secondary" || variant === "ghost") && "text-foreground",
    extra,
  );
}
