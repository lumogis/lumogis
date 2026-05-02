// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import type { ComponentType } from "react";
import { CalDavForm } from "./CalDavForm";
import { JsonFallbackForm } from "./JsonFallbackForm";
import { LlmApiKeyForm } from "./LlmApiKeyForm";
import { NtfyForm } from "./NtfyForm";

/* eslint-disable react-refresh/only-export-components -- registry exports are the public extension point for proprietary bundles. */

export interface CredentialFormProps {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  isEdit: boolean;
  hint?: string;
}

const registry: Record<string, ComponentType<CredentialFormProps>> = {
  caldav: CalDavForm,
  ntfy: NtfyForm,
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
    return <JsonFallbackForm value={props.value} onChange={props.onChange} hint={props.hint} />;
  };
}

export const credentialFormRegistry = registry;
export { JsonFallbackForm, CalDavForm, NtfyForm, LlmApiKeyForm };

// Proprietary bundles may `Object.assign(credentialFormRegistry, { ... })` per plan.
declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace LumogisProprietary {
    // hook for app bundle to extend
  }
}
