// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import type { NavigateFunction } from "react-router-dom";

export function buildAskAboutQuery(name: string): string {
  return `What do I know about ${name}?`;
}

export interface ChatPrefillState {
  prefill: string;
  wowDismissOnSend?: true;
}

export function navigateToChatWithPrefill(
  navigate: NavigateFunction,
  query: string,
  options: { wowDismissOnSend?: boolean } = {},
): void {
  const state: ChatPrefillState = {
    prefill: query,
    ...(options.wowDismissOnSend ? { wowDismissOnSend: true as const } : {}),
  };
  void navigate("/chat", { state });
}
