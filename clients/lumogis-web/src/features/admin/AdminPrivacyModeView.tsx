// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useQuery } from "@tanstack/react-query";

import { fetchAdminSettings } from "../../api/privacyMode";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PrivacyModePanel } from "./PrivacyModePanel";

export function AdminPrivacyModeView(): JSX.Element {
  const { client } = useAuth();
  const q = useQuery({
    queryKey: ["admin", "settings", "privacy"],
    queryFn: () => fetchAdminSettings(client),
  });

  if (q.isLoading) {
    return (
      <LoadingPlaceholder label="Loading privacy settings…" className="max-w-2xl">
        <SkeletonText lines={4} />
      </LoadingPlaceholder>
    );
  }
  if (q.isError) {
    return (
      <ErrorState message="Lumogis couldn't load the privacy settings." onRetry={() => void q.refetch()} />
    );
  }

  const data = q.data!;
  return (
    <section>
      <PrivacyModePanel
        initial={{
          privacy_mode: data.privacy_mode ?? "local_only",
          privacy_mode_locked: Boolean(data.privacy_mode_locked),
          privacy_effective: data.privacy_effective ?? data.privacy_mode ?? "local_only",
        }}
      />
    </section>
  );
}
