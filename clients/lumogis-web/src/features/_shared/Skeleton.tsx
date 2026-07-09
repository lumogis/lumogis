// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Shared skeleton / loading-placeholder primitives (LUM-212). Token-based, no
// extra dependency, and reduced-motion-aware (see Skeleton.module.css). Use
// `LoadingPlaceholder` to wrap skeletons so screen readers announce a single
// "Loading…" status while the shimmer blocks themselves stay aria-hidden.

import { type CSSProperties, type ReactNode } from "react";

import styles from "./Skeleton.module.css";

export interface SkeletonProps {
  /** CSS width, e.g. "100%", "8rem". Defaults to 100%. */
  width?: string;
  /** CSS height, e.g. "1rem", "120px". Defaults to the `.line` height. */
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
  const cls = [styles.skeleton, styles.line, className].filter(Boolean).join(" ");
  return <span className={cls} style={style} aria-hidden="true" />;
}

export interface SkeletonTextProps {
  /** Number of lines (default 3). The last line is rendered shorter. */
  lines?: number;
  className?: string;
}

/** A stack of text-line skeletons; the final line is shortened for realism. */
export function SkeletonText({ lines = 3, className }: SkeletonTextProps): JSX.Element {
  const count = Math.max(1, lines);
  const cls = [styles.lines, className].filter(Boolean).join(" ");
  return (
    <div className={cls} aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} width={i === count - 1 ? "60%" : "100%"} />
      ))}
    </div>
  );
}

export interface LoadingPlaceholderProps {
  /** Announced to screen readers via an sr-only live region. */
  label?: string;
  /** The (decorative) skeleton content. */
  children: ReactNode;
  className?: string;
}

/**
 * Accessible wrapper for any skeleton content: exposes a single polite
 * `role="status"` / `aria-busy` region with an sr-only label, while the visual
 * shimmer stays hidden from assistive tech.
 */
export function LoadingPlaceholder({
  label = "Loading…",
  children,
  className,
}: LoadingPlaceholderProps): JSX.Element {
  return (
    <div role="status" aria-busy="true" aria-live="polite" className={className}>
      <span className={styles.srOnly}>{label}</span>
      {children}
    </div>
  );
}
