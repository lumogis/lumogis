// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Host-side Postgres helpers for e2e specs (onboarding reset). CI publishes
// postgres via docker-compose.web-e2e-ci.yml; local runs may use compose exec.

import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../");

function composeFileArgs(): string {
  const raw =
    process.env.LUMOGIS_E2E_COMPOSE_FILES ?? "docker-compose.yml:docker-compose.web-e2e-ci.yml";
  return raw
    .split(":")
    .map((f) => f.trim())
    .filter(Boolean)
    .map((f) => `-f ${f}`)
    .join(" ");
}

/** Clear onboarding completion so the gate shows on next authenticated load. */
export function resetOnboardingCompletedAt(smokeEmail: string): void {
  const escaped = smokeEmail.replace(/'/g, "''");
  const sql = `UPDATE users SET onboarding_completed_at = NULL WHERE email = '${escaped}';`;
  const pgUser = process.env.POSTGRES_USER ?? "lumogis";
  const pgDb = process.env.POSTGRES_DB ?? "lumogis";
  const pgHost = process.env.POSTGRES_HOST ?? "127.0.0.1";
  const pgPort = process.env.POSTGRES_PORT ?? "5432";
  const pgPassword = process.env.POSTGRES_PASSWORD ?? "lumogis-dev";

  const run = (command: string, opts: { cwd?: string; env?: NodeJS.ProcessEnv } = {}): void => {
    execSync(command, {
      stdio: "pipe",
      timeout: 30_000,
      cwd: opts.cwd,
      env: opts.env ?? process.env,
    });
  };

  // Prefer host psql when CI overlay publishes postgres (see docker-compose.web-e2e-ci.yml).
  try {
    run(`psql -h ${pgHost} -p ${pgPort} -U ${pgUser} -d ${pgDb} -c ${JSON.stringify(sql)}`, {
      env: { ...process.env, PGPASSWORD: pgPassword },
    });
    return;
  } catch {
    // fall through — compose exec when host port is unavailable
  }

  const envFile = process.env.LUMOGIS_E2E_ENV_FILE ?? "config/test.env.example";
  try {
    run(
      `docker compose ${composeFileArgs()} --env-file ${envFile} exec -T postgres psql -U ${pgUser} -d ${pgDb} -c ${JSON.stringify(sql)}`,
      { cwd: REPO_ROOT },
    );
    return;
  } catch {
    throw new Error(
      `Could not reset users.onboarding_completed_at for ${smokeEmail}. ` +
        "Ensure the stack is up and POSTGRES_* reaches Postgres, or set LUMOGIS_E2E_COMPOSE_FILES.",
    );
  }
}
