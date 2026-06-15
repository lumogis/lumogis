// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { browser } from "@wdio/globals";

/** Register a mock IPC return value via @wdio/tauri-service. */
export async function mockInvokeReturn<T>(command: string, value: T): Promise<void> {
  const mock = await browser.tauri.mock(command);
  await mock.mockReturnValue(value);
}

/** Register a mock IPC implementation (async/sync). */
export async function mockInvokeImpl(
  command: string,
  impl: (...args: unknown[]) => unknown,
): Promise<void> {
  const mock = await browser.tauri.mock(command);
  await mock.mockImplementation(impl);
}

/** Register a mock IPC rejection. */
export async function mockInvokeReject(command: string, message: string): Promise<void> {
  const mock = await browser.tauri.mock(command);
  await mock.mockRejectedValue(new Error(message));
}
