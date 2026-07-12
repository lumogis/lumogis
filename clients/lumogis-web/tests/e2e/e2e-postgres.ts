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
  const escaped = smokeEmail.replace(/'/g, "''");
  runPsqlSql(`UPDATE users SET onboarding_completed_at = NULL WHERE email = '${escaped}';`, deps);
}

function runPsqlSql(sql: string, deps: { spawnSync?: SpawnSyncFn } = {}): void {
  const spawnSync = deps.spawnSync ?? nodeSpawnSync;
  const pgUser = process.env.POSTGRES_USER ?? "lumogis";
  const pgDb = process.env.POSTGRES_DB ?? "lumogis";
  const pgHost = process.env.POSTGRES_HOST ?? "127.0.0.1";
  const pgPort = process.env.POSTGRES_PORT ?? "5432";
  const pgPassword = process.env.POSTGRES_PASSWORD ?? "lumogis-dev";

  const psqlArgs = ["-h", pgHost, "-p", String(pgPort), "-U", pgUser, "-d", pgDb, "-c", sql];
  const pgEnv = { ...process.env, PGPASSWORD: pgPassword };

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
    "Could not run psql for e2e helper. Ensure the stack is up and POSTGRES_* reaches Postgres.",
  );
}

/**
 * Clear wow-path dismissal so cards may show on next `/chat` load (LUM-216).
 */
export function resetWowDismissedAt(
  smokeEmail: string,
  deps: { spawnSync?: SpawnSyncFn } = {},
): void {
  const escaped = smokeEmail.replace(/'/g, "''");
  runPsqlSql(`UPDATE users SET wow_dismissed_at = NULL WHERE email = '${escaped}';`, deps);
}

/**
 * Ensure onboarding is complete for wow gate eligibility (LUM-216 e2e).
 */
export function ensureOnboardingCompletedAt(
  smokeEmail: string,
  deps: { spawnSync?: SpawnSyncFn } = {},
): void {
  const escaped = smokeEmail.replace(/'/g, "''");
  runPsqlSql(
    `UPDATE users SET onboarding_completed_at = COALESCE(onboarding_completed_at, NOW()) WHERE email = '${escaped}';`,
    deps,
  );
}

/**
 * Seed non-staged personal entities for the smoke user (LUM-216).
 */
/**
 * Simulate a failed index attempt for LUM-608 retry e2e (live stack).
 * Sets `status=failed` + `last_error` as the orchestrator would after embed/Qdrant failure.
 */
export function markCaptureIndexFailed(
  captureId: string,
  smokeEmail: string,
  lastError = "index_memory_unavailable",
  deps: { spawnSync?: SpawnSyncFn } = {},
): void {
  const escapedEmail = smokeEmail.replace(/'/g, "''");
  const escapedError = lastError.replace(/'/g, "''");
  const escapedId = captureId.replace(/'/g, "''");
  runPsqlSql(
    `UPDATE captures c SET status = 'failed', last_error = '${escapedError}', updated_at = NOW()
     FROM users u
     WHERE c.user_id = u.id AND u.email = '${escapedEmail}' AND c.id = '${escapedId}'::uuid
       AND c.status IN ('pending', 'failed');`,
    deps,
  );
}

export function seedWowEntitiesForSmokeUser(
  smokeEmail: string,
  count = 3,
  deps: { spawnSync?: SpawnSyncFn } = {},
): void {
  const escaped = smokeEmail.replace(/'/g, "''");
  const n = Math.max(1, Math.min(count, 10));
  runPsqlSql(
    `INSERT INTO entities (entity_id, user_id, name, entity_type, mention_count, scope, is_staged)
     SELECT gen_random_uuid(), u.id, 'E2E Wow Entity ' || gs.n, 'Person', gs.n, 'personal', FALSE
     FROM users u
     CROSS JOIN generate_series(1, ${n}) AS gs(n)
     WHERE u.email = '${escaped}';`,
    deps,
  );
}
