// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Notices about the whole app, at the top of the page (LUM-993 Pass 2a): offline,
// and "this Lumogis has a secure address". Page-specific notices (chat service
// degradation, model loading) stay on their page.

import { useOnlineStatus } from "../../pwa/useOnlineStatus";
import { OfflineBanner } from "../OfflineBanner";
import { SecureAddressNotice } from "../SecureAddressNotice";

export function ShellBanners(): JSX.Element {
  const online = useOnlineStatus();
  return (
    <div className="flex flex-col gap-2 empty:hidden [&:not(:empty)]:mb-4">
      <OfflineBanner visible={!online} />
      <SecureAddressNotice />
    </div>
  );
}
