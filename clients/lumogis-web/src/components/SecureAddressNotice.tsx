// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-331 — a quiet notice at the top of the page (ShellBanners, LUM-993): "this Lumogis has a secure
// address". Shown to any signed-in member, but only when
//   - the page is not already on https (never on the secure address itself),
//   - the page was opened by a home network address or localhost (the device
//     check is meaningless from anywhere else; see pwa/secureAddressCheck.ts),
//   - the server has a working address, and
//   - this device actually opened it (the check passed).
// So it never sends anyone to an address their router hides. Dismissing it is
// remembered per address on this device.

import { useEffect, useState } from "react";

import { ShieldCheck } from "lucide-react";

import { fetchPublicSecureAddress } from "../api/secureAddress";
import { useAuth } from "../auth/AuthProvider";
import { canCheckFromHere, checkReachable, isSecurePageOrigin } from "../pwa/secureAddressCheck";
import { Alert, AlertAction, AlertDescription } from "./ui/alert";
import { Button } from "./ui/button";

export const SECURE_ADDRESS_NOTICE_DISMISS_KEY = "lumogis:secure-address-notice-dismissed";

export const SECURE_ADDRESS_NOTICE_COPY =
  "This Lumogis has a secure address. Voice input, installing the app and notifications work there. You'll sign in again at the new address.";

function dismissedFor(url: string): boolean {
  try {
    return localStorage.getItem(SECURE_ADDRESS_NOTICE_DISMISS_KEY) === url;
  } catch {
    return false;
  }
}

export interface SecureAddressNoticeProps {
  /** The page location; tests pass one, the shell uses window.location. */
  loc?: Pick<Location, "protocol" | "hostname">;
}

export function SecureAddressNotice({ loc }: SecureAddressNoticeProps = {}): JSX.Element | null {
  const { client, status } = useAuth();
  const here = loc ?? window.location;
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (status !== "authenticated") return;
    if (isSecurePageOrigin(here) || !canCheckFromHere(here)) return;
    let cancelled = false;
    void (async () => {
      try {
        const body = await fetchPublicSecureAddress(client);
        if (cancelled || !body.url || dismissedFor(body.url)) return;
        if (await checkReachable(body.url)) {
          if (!cancelled) setUrl(body.url);
        }
      } catch {
        /* no secure address, an older server, or offline: show nothing */
      }
    })();
    return () => {
      cancelled = true;
    };
    // `here` is stable for a page view; a new origin is a new page load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, status]);

  if (!url) return null;

  return (
    <aside aria-label="Secure address" data-testid="lumogis-secure-address-notice">
      {/* A notice, not an alarm: drop the Alert's role="alert". */}
      <Alert role={undefined} className="border-accent-text/30 bg-accent-soft/40">
      <ShieldCheck aria-hidden="true" />
      <AlertDescription className="text-foreground">
        {SECURE_ADDRESS_NOTICE_COPY} <a href={url}>Open {url}</a>
      </AlertDescription>
      <AlertAction>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            try {
              localStorage.setItem(SECURE_ADDRESS_NOTICE_DISMISS_KEY, url);
            } catch {
              /* storage blocked — hide for this page view only */
            }
            setUrl(null);
          }}
        >
          Dismiss
        </Button>
      </AlertAction>
      </Alert>
    </aside>
  );
}
