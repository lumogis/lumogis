// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Entity household-sharing hooks (LUM-581).
//
// The `memory` feature is hand-rolled `useState`/`useEffect` (no React-Query,
// unlike `features/documents`). So these hooks mirror the documents mutation
// SHAPE (`{ mutateAsync, isPending }`) for a uniform `EntityShareToggle`, but
// are implemented with `useState` and rely on the caller re-running its fetch
// effect after a successful publish/unpublish (there is no query cache to
// invalidate here).

import { useCallback, useState } from "react";

import type { ApiClient } from "../../api/client";
import {
  publishEntity,
  unpublishEntity,
  type EntityShareStatus,
} from "../../api/search";

export interface EntityShareMutation {
  mutateAsync: (entityId: string) => Promise<void>;
  isPending: boolean;
}

export function usePublishEntity(client: ApiClient): EntityShareMutation {
  const [isPending, setIsPending] = useState(false);
  const mutateAsync = useCallback(
    async (entityId: string) => {
      setIsPending(true);
      try {
        await publishEntity(client, entityId);
      } finally {
        setIsPending(false);
      }
    },
    [client],
  );
  return { mutateAsync, isPending };
}

export function useUnpublishEntity(client: ApiClient): EntityShareMutation {
  const [isPending, setIsPending] = useState(false);
  const mutateAsync = useCallback(
    async (entityId: string) => {
      setIsPending(true);
      try {
        await unpublishEntity(client, entityId);
      } finally {
        setIsPending(false);
      }
    },
    [client],
  );
  return { mutateAsync, isPending };
}

export function entityShareStatusLabel(
  status: EntityShareStatus | undefined,
): string {
  switch (status) {
    case "shared":
      return "Shared";
    case "personal":
    default:
      return "Personal";
  }
}
