// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import type { ComponentType } from "react";
import { CalDavForm } from "./CalDavForm";
import { ImapForm } from "./ImapForm";
import { JsonFallbackForm } from "./JsonFallbackForm";
import { LlmApiKeyForm } from "./LlmApiKeyForm";
import { NtfyForm } from "./NtfyForm";
import { PaperlessForm } from "./PaperlessForm";

/* eslint-disable react-refresh/only-export-components -- registry exports are the public extension point for proprietary bundles. */

export interface CredentialFormProps {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  isEdit: boolean;
  hint?: string;
}

const registry: Record<string, ComponentType<CredentialFormProps>> = {
  caldav: CalDavForm,
  imap: ImapForm,
  ntfy: NtfyForm,
  paperless: PaperlessForm,
};

function LlmWrapper(props: CredentialFormProps): JSX.Element {
  return <LlmApiKeyForm value={props.value} onChange={props.onChange} />;
}

// register llm_* at runtime
export function getCredentialFormComponent(connector: string): ComponentType<CredentialFormProps> {
  if (connector.startsWith("llm_")) return LlmWrapper;
  const c = registry[connector];
  if (c) return c;
  return function Fallback(props: CredentialFormProps) {
    // `isEdit` is threaded on purpose (LUM-846): the fallback needs it to warn
    // that saving replaces the whole payload, and to refuse an empty object —
    // and it was the one prop this wrapper dropped.
    return (
      <JsonFallbackForm
        value={props.value}
        onChange={props.onChange}
        hint={props.hint}
        isEdit={props.isEdit}
      />
    );
  };
}

export const credentialFormRegistry = registry;
export { JsonFallbackForm, CalDavForm, ImapForm, NtfyForm, PaperlessForm, LlmApiKeyForm };

// Proprietary bundles may `Object.assign(credentialFormRegistry, { ... })` per plan.
declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace LumogisProprietary {
    // hook for app bundle to extend
  }
}
