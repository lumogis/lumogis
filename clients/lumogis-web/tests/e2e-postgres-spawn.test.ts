// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi } from "vitest";

import { resetOnboardingCompletedAt } from "./e2e/e2e-postgres";

describe("resetOnboardingCompletedAt", () => {
  it("passes SQL to spawnSync argv so $(...) in smoke email is not executed by a shell", () => {
    const marker = "MARKER_SHOULD_NOT_RUN_IN_EMAIL";
    const malicious = `smoke@$(echo ${marker})x@example.com`;

    const spawnSync = vi
      .fn()
      .mockImplementationOnce((cmd, args, opts) => {
        expect(cmd).toBe("psql");
        expect((opts as { shell?: boolean } | undefined)?.shell).not.toBe(true);
        const argv = args as string[];
        const cIdx = argv.indexOf("-c");
        expect(cIdx).toBeGreaterThanOrEqual(0);
        expect(argv[cIdx + 1]).toContain(`$(echo ${marker})`);
        return {
          status: 1,
          stdout: Buffer.alloc(0),
          stderr: Buffer.alloc(0),
          error: undefined,
        };
      })
      .mockImplementationOnce((cmd, args, opts) => {
        expect(cmd).toBe("docker");
        expect((opts as { shell?: boolean } | undefined)?.shell).not.toBe(true);
        const argv = args as string[];
        const cIdx = argv.indexOf("-c");
        expect(cIdx).toBeGreaterThanOrEqual(0);
        expect(argv[cIdx + 1]).toContain(`$(echo ${marker})`);
        return {
          status: 0,
          stdout: Buffer.alloc(0),
          stderr: Buffer.alloc(0),
          error: undefined,
        };
      });

    resetOnboardingCompletedAt(malicious, { spawnSync });
    expect(spawnSync).toHaveBeenCalledTimes(2);
  });
});
