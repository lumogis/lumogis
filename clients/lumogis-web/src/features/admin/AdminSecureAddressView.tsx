// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin → Secure address (LUM-331): turns on a free https name for this Lumogis
// on the home network, shows where it is, and walks through the router step
// when a router hides it. The server derives the state; the words live in
// secureAddressCopy.ts and routerGuide.ts. The page polls every 3 s while
// Lumogis is still getting a name or a certificate.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError } from "../../api/client";
import {
  detectSecureAddress,
  disableSecureAddress,
  enableSecureAddress,
  fetchSecureAddress,
  retrySecureAddress,
  setSecureAddressLan,
  type SecureAddressStatus,
} from "../../api/secureAddress";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { canCheckFromHere, checkReachable } from "../../pwa/secureAddressCheck";
import { formatDateTime } from "../../util/formatTimestamp";
import { ROUTER_GUIDES, ROUTER_REBOOT_NOTE } from "./routerGuide";
import {
  CADDY_LOGS_COMMAND,
  DETECTING_TEXT,
  NAME_NEW_CHECK_FAILED,
  NAME_NEW_REFETCH_MS,
  SECURE_ADDRESS_DOCS_URL,
  SOURCE_TEXT,
  WARNING_TEXT,
  isNameNew,
  secureAddressCopy,
} from "./secureAddressCopy";

const SECURE_ADDRESS_QUERY_KEY = ["admin", "secure-address"] as const;
const SECURE_ADDRESS_POLL_MS = 3000;

function mutationError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 400) {
      return "That is not a home network address. Use the address that starts with 10., 172.16 to 172.31., or 192.168.";
    }
    if (e.status === 409 && e.detail === "busy") {
      return "Lumogis is busy with the secure address. Try again in a moment.";
    }
    if (e.status === 409) return "This page was out of date. It now shows the current state.";
    if (e.status === 403) return "Admin role required.";
    return e.detail;
  }
  return "Something went wrong. Try again.";
}

type CheckResult = "idle" | "checking" | "ok" | "failed";

export function AdminSecureAddressView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: SECURE_ADDRESS_QUERY_KEY,
    queryFn: () => fetchSecureAddress(client),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      if (secureAddressCopy(data).poll) return SECURE_ADDRESS_POLL_MS;
      // `ready` does not poll; while the name is new, check once a minute so the copy ends with the window.
      return isNameNew(data) ? NAME_NEW_REFETCH_MS : false;
    },
  });

  const [address, setAddress] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOff, setConfirmOff] = useState(false);
  const [changing, setChanging] = useState(false);
  const [copied, setCopied] = useState(false);
  const [check, setCheck] = useState<CheckResult>("idle");
  // The router steps open by themselves when the router is the problem; once the admin
  // opens or closes them, that choice wins.
  const [routerOpen, setRouterOpen] = useState<boolean | null>(null);

  // True once the admin has typed in the address input; a suggestion never replaces typed text.
  const [edited, setEdited] = useState(false);
  // Whether the last rendered data showed an address form (see the auto-detect effect).
  const formShown = useRef(false);
  const form = q.data ? secureAddressCopy(q.data).form : null;

  // Look for this computer's address. The response is not written into the cache: a detect that
  // returns after a Turn on would put the older `off` status back. The refetched GET carries the
  // suggestion. Errors are swallowed: the form still works by typing (409 busy = another tab).
  const detect = useMutation({
    mutationFn: () => detectSecureAddress(client),
    onSuccess: () => qc.invalidateQueries({ queryKey: SECURE_ADDRESS_QUERY_KEY }),
  });

  // Detect once each time a form first appears, not on every refetch.
  useEffect(() => {
    if (form !== null && !formShown.current) detect.mutate();
    formShown.current = form !== null;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires on the form appearing only
  }, [form]);

  const onDone = (data: SecureAddressStatus) => {
    qc.setQueryData(SECURE_ADDRESS_QUERY_KEY, data);
    setError(null);
    setConfirmed(false);
    setEdited(false);
    setChanging(false);
    setConfirmOff(false);
    setCheck("idle");
  };
  const onFail = async (e: unknown) => {
    setError(mutationError(e));
    if (e instanceof ApiError && e.status === 409 && e.detail !== "busy") {
      await qc.invalidateQueries({ queryKey: SECURE_ADDRESS_QUERY_KEY });
    }
  };

  const enable = useMutation({
    mutationFn: (lan: string) => enableSecureAddress(client, lan),
    onSuccess: onDone,
    onError: onFail,
  });
  const setLan = useMutation({
    mutationFn: (lan: string) => setSecureAddressLan(client, lan),
    onSuccess: onDone,
    onError: onFail,
  });
  const retry = useMutation({
    mutationFn: () => retrySecureAddress(client),
    onSuccess: onDone,
    onError: onFail,
  });
  const disable = useMutation({
    mutationFn: () => disableSecureAddress(client),
    onSuccess: onDone,
    onError: onFail,
  });
  const busy = enable.isPending || setLan.isPending || retry.isPending || disable.isPending;

  // Prefill with the address Lumogis suggests; the admin still has to confirm it. A newer
  // suggestion replaces an older prefill but never typed text, and a null one never clears the
  // input. A tick given for one address does not carry over to another. Left alone while a
  // request is in flight; `busy` is a dependency because a mutation's `onSuccess` (which writes
  // the next status) runs before `isPending` clears, so the new suggestion is applied when it ends.
  const suggested = q.data?.suggested_address ?? q.data?.lan_address ?? null;
  useEffect(() => {
    if (suggested === null || edited || busy || suggested === address) return;
    setAddress(suggested);
    setConfirmed(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reacts to a new suggestion or a request ending
  }, [suggested, busy]);

  if (q.isPending) {
    return (
      <LoadingPlaceholder label="Loading…" className="max-w-2xl">
        <SkeletonText lines={5} />
      </LoadingPlaceholder>
    );
  }

  if (q.isError) {
    const err = q.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Not signed in or session expired."
          : err.status === 403
            ? "Admin role required for the secure address page."
            : err.detail
        : "Secure address status unavailable.";
    return <ErrorState message={detail} onRetry={() => void q.refetch()} />;
  }

  const s = q.data;
  const copy = secureAddressCopy(s);
  const showForm = copy.form !== null || changing;
  const formMode = copy.form ?? "set_address";
  const checkHere = canCheckFromHere();

  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    const lan = address.trim();
    if (formMode === "enable") enable.mutate(lan);
    else setLan.mutate(lan);
  };

  const copyAddress = async () => {
    if (!s.url) return;
    try {
      await navigator.clipboard.writeText(s.url);
      setCopied(true);
    } catch {
      /* clipboard blocked — the address is on screen to copy by hand */
    }
  };

  const runCheck = async () => {
    if (!s.url) return;
    setCheck("checking");
    setCheck((await checkReachable(s.url)) ? "ok" : "failed");
  };

  const toneClass =
    copy.tone === "warn"
      ? "border-destructive/40 text-destructive"
      : "border-border bg-muted text-foreground";

  return (
    <section className="flex flex-col gap-2">
      <p
        role="status"
        data-tone={copy.tone}
        className={`m-0 max-w-2xl rounded-lg border px-3 py-2 text-sm font-medium ${toneClass}`}
      >
        {copy.headline}
      </p>
      {copy.details.map((line) => (
        <p key={line} className="m-0 max-w-2xl text-sm">
          {line}
        </p>
      ))}
      {s.name_changed ? (
        <p className="m-0 max-w-2xl text-sm">
          This Lumogis has a new secure address, because its key was lost (for example after the
          workspace was wiped). The old address no longer works.
        </p>
      ) : null}
      {s.warnings.length > 0 ? (
        <ul aria-label="Warnings" className="m-0 max-w-2xl list-disc pl-5 text-sm">
          {s.warnings.map((w) => (
            <li key={w}>{WARNING_TEXT[w]}</li>
          ))}
        </ul>
      ) : null}

      {error ? (
        <Alert variant="destructive" className="max-w-2xl">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {copy.showAddress && s.url ? (
        <PageSection title="Your address">
          <p className="mt-0 text-sm break-all">
            <a
              href={s.url}
              data-testid="secure-address-url"
              className="text-accent-text underline underline-offset-2"
            >
              {s.url}
            </a>
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" onClick={() => void copyAddress()}>
              {copied ? "Copied" : "Copy address"}
            </Button>
            {checkHere ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => void runCheck()}
                disabled={check === "checking"}
              >
                Check on this device
              </Button>
            ) : null}
          </div>
          {!checkHere ? (
            <p className="text-sm">To check it, open the address on a phone or laptop on your home Wi-Fi.</p>
          ) : null}
          {check === "checking" ? (
            <p role="status" className="text-sm">
              Checking…
            </p>
          ) : null}
          {check === "ok" ? (
            <p role="status" className="text-sm">
              This device opens the secure address.
            </p>
          ) : null}
          {check === "failed" ? (
            <p role="status" className="text-sm">
              {isNameNew(s)
                ? NAME_NEW_CHECK_FAILED
                : 'This device cannot open the secure address. If other sites work, your router probably hides it: see "Allow it in your router" below.'}
            </p>
          ) : null}
          <p className="text-sm">
            Everyone signs in again at the new address, once per device. Lumogis keeps working at the
            old address too.
          </p>
          {s.certificate_valid_until ? (
            <p className="mb-0 text-sm text-muted-foreground">
              Certificate valid until {formatDateTime(s.certificate_valid_until, { withTime: false })}.
              Lumogis renews it on its own.
            </p>
          ) : null}
        </PageSection>
      ) : null}

      {s.state !== "off" && s.lan_address ? (
        <p className="m-0 flex max-w-2xl flex-wrap items-center gap-x-2 text-sm">
          <span>
            Home network address: <strong>{s.lan_address}</strong>
            {s.lan_address_source ? ` (${SOURCE_TEXT[s.lan_address_source]})` : null}
          </span>
          {!showForm ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setAddress(s.lan_address ?? "");
                setEdited(true);
                setConfirmed(false);
                setChanging(true);
              }}
            >
              Change
            </Button>
          ) : null}
        </p>
      ) : null}

      {showForm ? (
        <PageSection title="Home network address">
          <form onSubmit={submit} aria-label="Home network address" className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="secure-address-lan">This computer's address on your home network</Label>
              <Input
                id="secure-address-lan"
                type="text"
                inputMode="decimal"
                autoComplete="off"
                placeholder="192.168.1.20"
                value={address}
                onChange={(e) => {
                  setAddress(e.target.value);
                  setEdited(true);
                  setConfirmed(false);
                }}
              />
            </div>
            {s.suggested_address && address.trim() === s.suggested_address ? (
              <p className="m-0 text-sm text-muted-foreground">Lumogis found this address. Check that it is right.</p>
            ) : null}
            {detect.isPending ? (
              <p role="status" className="m-0 text-sm">
                {DETECTING_TEXT}
              </p>
            ) : (
              <div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => detect.mutate()}
                  disabled={busy || detect.isPending}
                >
                  Look again
                </Button>
              </div>
            )}
            <div className="flex items-start gap-2">
              <Checkbox
                id="secure-address-confirm"
                checked={confirmed}
                onCheckedChange={(checked) => setConfirmed(checked === true)}
              />
              <Label htmlFor="secure-address-confirm" className="font-normal">
                Yes, my phone and laptop open Lumogis at this address.
              </Label>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={!confirmed || address.trim() === "" || busy}>
                {formMode === "enable" ? "Turn on secure address" : "Save address"}
              </Button>
              {changing ? (
                <Button type="button" variant="ghost" onClick={() => setChanging(false)}>
                  Cancel
                </Button>
              ) : null}
            </div>
          </form>
        </PageSection>
      ) : null}

      {copy.showRetry || copy.showTurnOff ? (
        <div className="flex flex-wrap gap-2">
          {copy.showRetry ? (
            <Button type="button" onClick={() => retry.mutate()} disabled={busy}>
              Retry
            </Button>
          ) : null}
          {copy.showTurnOff ? (
            <Button type="button" variant="destructive" onClick={() => setConfirmOff(true)} disabled={busy}>
              Turn off
            </Button>
          ) : null}
        </div>
      ) : null}

      <AlertDialog open={confirmOff} onOpenChange={setConfirmOff}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Turn off the secure address?</AlertDialogTitle>
            <AlertDialogDescription>
              Devices that use it go back to the plain address, and the name is removed from Lumogis's
              name service.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it on</AlertDialogCancel>
            <AlertDialogAction
              variant="destructiveSolid"
              disabled={busy}
              onClick={() => {
                setConfirmOff(false);
                disable.mutate();
              }}
            >
              Yes, turn it off
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {copy.showAddress ? (
        <Collapsible
          className="max-w-2xl"
          open={routerOpen ?? copy.showRouterGuide}
          onOpenChange={setRouterOpen}
        >
          <h2 className="m-0 text-base font-semibold">
            <CollapsibleTrigger
              render={<Button type="button" variant="ghost" className="-ml-2 group/trigger text-base font-semibold" />}
            >
              <ChevronDown
                aria-hidden="true"
                className="transition-transform group-aria-expanded/trigger:rotate-180"
              />
              Allow it in your router
            </CollapsibleTrigger>
          </h2>
          <CollapsibleContent keepMounted className="flex flex-col gap-4 pt-2">
            {ROUTER_GUIDES.map((g) => (
              <div key={g.id}>
                <h3 className="m-0 mb-1 text-sm font-semibold">{g.name}</h3>
                <ol className="m-0 flex list-decimal flex-col gap-1 pl-5 text-sm">
                  {g.steps.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
              </div>
            ))}
            <p className="m-0 text-sm">{ROUTER_REBOOT_NOTE}</p>
          </CollapsibleContent>
        </Collapsible>
      ) : null}

      {copy.showLogsHint ? (
        <Collapsible className="max-w-2xl">
          <CollapsibleTrigger
            render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
          >
            <ChevronDown aria-hidden="true" className="transition-transform group-aria-expanded/trigger:rotate-180" />
            Details
          </CollapsibleTrigger>
          <CollapsibleContent keepMounted className="pt-2 text-sm">
            <p className="m-0">
              Run this in the Lumogis folder: <code>{CADDY_LOGS_COMMAND}</code>
            </p>
          </CollapsibleContent>
        </Collapsible>
      ) : null}

      <p className="m-0 text-sm">
        <a
          href={SECURE_ADDRESS_DOCS_URL}
          target="_blank"
          rel="noreferrer"
          className="text-accent-text underline underline-offset-2"
        >
          Secure address at home (full guide)
        </a>
      </p>
    </section>
  );
}
