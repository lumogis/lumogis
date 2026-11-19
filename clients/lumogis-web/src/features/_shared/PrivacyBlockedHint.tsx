// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-881 — one calm line explaining that cloud models are off because privacy
// mode is local-only (ADR 147), with a link for people who can change it.
// Callers mount it only when `catalogHasPrivacyBlock(models)` holds.

import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { fetchMePrivacyMode } from "../../api/privacyMode";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { useAuth, useUser } from "../../auth/AuthProvider";
import { privacyBlockCopy, type PrivacyHintSurface } from "./privacyBlockCopy";

export function PrivacyBlockedHint({
  surface,
  adminTier = false,
}: {
  surface: PrivacyHintSurface;
  adminTier?: boolean;
}): JSX.Element | null {
  const { client } = useAuth();
  const isAdmin = useUser()?.role === "admin";
  // Same key as the Me page, so the two share cache and invalidation.
  const q = useQuery({
    queryKey: ["me", "privacy-mode"],
    queryFn: () => fetchMePrivacyMode(client),
  });

  if (q.isPending) return null;
  const copy = privacyBlockCopy({ me: q.isError ? null : (q.data ?? null), isAdmin, surface, adminTier });
  if (copy === null) return null;

  return (
    <Alert role="status" data-testid="privacy-blocked-hint">
      <AlertDescription>
        {copy.text}
        {copy.linkTo !== null && copy.linkLabel !== null ? (
          <>
            {" "}
            <Link to={copy.linkTo} className="text-accent-text underline underline-offset-2">
              {copy.linkLabel}
            </Link>
          </>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
