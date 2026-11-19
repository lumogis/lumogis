// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The app's one button (LUM-993). A thin wrapper over the shadcn `ui/button`, kept
// so the 17 files that already use `<Button variant=…>` get the new look without
// changing. New code may use `ui/button` directly. Links that must look like a
// button use `legacyButtonClasses` from ./buttonClasses.

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { Button as UiButton } from "@/components/ui/button";

import { SIZE, VARIANT, type ButtonSize, type ButtonVariant } from "./buttonClasses";

export type { ButtonSize, ButtonVariant } from "./buttonClasses";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", type = "button", ...rest },
  ref,
) {
  return (
    <UiButton
      ref={ref}
      type={type}
      variant={VARIANT[variant]}
      size={SIZE[size]}
      data-variant={variant}
      {...rest}
    />
  );
});
