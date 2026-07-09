// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useQuery } from "@tanstack/react-query";

import { fetchAdminSettings } from "../../api/privacyMode";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { PrivacyModePanel } from "./PrivacyModePanel";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return "Request failed";
}

export function AdminPrivacyModeView(): JSX.Element {
  const { client } = useAuth();
  const q = useQuery({
    queryKey: ["admin", "settings", "privacy"],
    queryFn: () => fetchAdminSettings(client),
  });

  if (q.isLoading) return <p>Loading privacy settings…</p>;
  if (q.isError) return <p role="alert">{errMsg(q.error)}</p>;

  const data = q.data!;
  return (
    <section>
      <h1>Privacy mode</h1>
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
