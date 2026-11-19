// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Loading placeholders (LUM-212 behaviour, LUM-993 look). Same exports and props
// as before; `features/_shared/Skeleton.tsx` re-exports these. The shimmer blocks
// stay hidden from assistive tech; one polite status announces the load.

import { type CSSProperties, type ReactNode } from "react";

import { cn } from "cn";

export interface SkeletonProps {
  /** CSS width, e.g. "100%", "8rem". Defaults to 100%. */
  width?: string;
  /** CSS height, e.g. "1rem", "120px". Defaults to one text line. */
  height?: string;
  /** Override border-radius (e.g. "50%" for an avatar). */
  radius?: string;
  className?: string;
}

/** A single shimmer block. Decorative — hidden from assistive tech. */
export function Skeleton({ width, height, radius, className }: SkeletonProps): JSX.Element {
  const style: CSSProperties = {};
  if (width !== undefined) style.width = width;
  if (height !== undefined) style.height = height;
  if (radius !== undefined) style.borderRadius = radius;
  return (
    <span
      className={cn(
        "block h-4 w-full animate-pulse rounded-md bg-muted motion-reduce:animate-none",
        className,
      )}
      style={style}
      aria-hidden="true"
    />
  );
}

export interface SkeletonTextProps {
  /** Number of lines (default 3). The last line is rendered shorter. */
  lines?: number;
  className?: string;
}

/** A stack of text-line skeletons; the final line is shortened for realism. */
export function SkeletonText({ lines = 3, className }: SkeletonTextProps): JSX.Element {
  const count = Math.max(1, lines);
  return (
    <div className={cn("flex flex-col gap-2", className)} aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} width={i === count - 1 ? "60%" : "100%"} />
      ))}
    </div>
  );
}

export interface LoadingPlaceholderProps {
  /** Announced to screen readers via a visually hidden live region. */
  label?: string;
  /** The (decorative) skeleton content. */
  children: ReactNode;
  className?: string;
}

/**
 * Accessible wrapper for any skeleton content: one polite `role="status"` /
 * `aria-busy` region with a visually hidden label; the shimmer stays hidden.
 */
export function LoadingPlaceholder({
  label = "Loading…",
  children,
  className,
}: LoadingPlaceholderProps): JSX.Element {
  return (
    <div role="status" aria-busy="true" aria-live="polite" className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}
