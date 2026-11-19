// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The empty chat (LUM-993 Pass 2b): a heading and the composer, centred, then
// the empty-region content in Decision 8's order: the library card when the
// library is empty, otherwise the suggestion chips when there are any. The
// history line with its (i) sits under the composer on every chat screen
// (ChatHistoryNote), so it is not repeated here.

import type { ReactNode } from "react";

export const CHAT_HOME_HEADING = "What can I help you with?";

export interface ChatHomeProps {
  composer: ReactNode;
  /** Shown under the composer: the library card or the suggestion chips. */
  below?: ReactNode;
}

export function ChatHome({ composer, below }: ChatHomeProps): JSX.Element {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-6 px-1 py-8" data-testid="chat-home">
      <h1 className="m-0 text-center text-2xl font-semibold tracking-tight md:text-3xl">{CHAT_HOME_HEADING}</h1>
      {composer}
      {below ? <div className="flex flex-col gap-4">{below}</div> : null}
    </div>
  );
}
