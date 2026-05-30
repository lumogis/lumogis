// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Host-side Postgres helpers for e2e specs (onboarding reset). CI publishes
// postgres via docker-compose.web-e2e-ci.yml; local runs may use compose exec.

import { spawnSync as nodeSpawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../");

export type SpawnSyncFn = typeof nodeSpawnSync;

function composeFileList(): string[] {
  const raw =
    process.env.LUMOGIS_E2E_COMPOSE_FILES ?? "docker-compose.yml:docker-compose.web-e2e-ci.yml";
  return raw
    .split(":")
    .map((f) => f.trim())
    .filter(Boolean);
}

function spawnOk(
  spawnSync: SpawnSyncFn,
  command: string,
  args: string[],
  opts: { cwd?: string; env?: NodeJS.ProcessEnv } = {},
): boolean {
  const r = spawnSync(command, args, {
    stdio: "pipe",
    timeout: 30_000,
    cwd: opts.cwd,
    env: opts.env ?? process.env,
  });
  return r.error === undefined && r.status === 0;
}

/**
 * Clear onboarding completion so the gate shows on next authenticated load.
 *
 * @param deps.spawnSync — optional override for tests (defaults to `node:child_process.spawnSync`).
 */
export function resetOnboardingCompletedAt(
  smokeEmail: string,
  deps: { spawnSync?: SpawnSyncFn } = {},
): void {
  const spawnSync = deps.spawnSync ?? nodeSpawnSync;
  const escaped = smokeEmail.replace(/'/g, "''");
  const sql = `UPDATE users SET onboarding_completed_at = NULL WHERE email = '${escaped}';`;
  const pgUser = process.env.POSTGRES_USER ?? "lumogis";
  const pgDb = process.env.POSTGRES_DB ?? "lumogis";
  const pgHost = process.env.POSTGRES_HOST ?? "127.0.0.1";
  const pgPort = process.env.POSTGRES_PORT ?? "5432";
  const pgPassword = process.env.POSTGRES_PASSWORD ?? "lumogis-dev";

  const psqlArgs = ["-h", pgHost, "-p", String(pgPort), "-U", pgUser, "-d", pgDb, "-c", sql];
  const pgEnv = { ...process.env, PGPASSWORD: pgPassword };

  // Prefer host psql when CI overlay publishes postgres (see docker-compose.web-e2e-ci.yml).
  if (spawnOk(spawnSync, "psql", psqlArgs, { env: pgEnv })) {
    return;
  }

  const envFile = process.env.LUMOGIS_E2E_ENV_FILE ?? "config/test.env.example";
  const files = composeFileList();
  const composeArgs: string[] = ["compose"];
  for (const f of files) {
    composeArgs.push("-f", f);
  }
  composeArgs.push(
    "--env-file",
    envFile,
    "exec",
    "-T",
    "postgres",
    "psql",
    "-U",
    pgUser,
    "-d",
    pgDb,
    "-c",
    sql,
  );

  if (spawnOk(spawnSync, "docker", composeArgs, { cwd: REPO_ROOT })) {
    return;
  }

  throw new Error(
    `Could not reset users.onboarding_completed_at for ${smokeEmail}. ` +
      "Ensure the stack is up and POSTGRES_* reaches Postgres, or set LUMOGIS_E2E_COMPOSE_FILES.",
  );
}
