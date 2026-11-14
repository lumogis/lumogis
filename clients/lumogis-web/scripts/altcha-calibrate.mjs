// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Calibrate `LUMOGIS_ALTCHA_COST` against the real solver.
//
//   cd clients/lumogis-web && node scripts/altcha-calibrate.mjs
//
// Run this before changing the cost — ADR 209's revisit condition asks for a
// measurement, not a guess. **Run it on an idle machine.** The first attempt at
// this calibration was taken while three test suites were saturating the CPU
// and produced non-monotonic nonsense (cost 400 reading slower than 800), which
// is the kind of number that gets a security parameter set wrong.
//
// LUM-65 P0 #1 — v2's `cost` is a per-candidate KDF iteration count, not v1's search space, and
// with keyPrefix "00" the expected total work is cost x ~256. The shipped 50_000
// was carried across from a v1 number. Median of N runs per cost, because the
// solve is a search and single samples are worthless.
import { sha, solveChallenge } from "altcha/lib";
import crypto from "crypto";

const RUNS = 7;
const COSTS = [100, 200, 400, 800, 1600];

function mk(cost) {
  const nonce = crypto.randomBytes(16).toString("hex");
  const salt = crypto.randomBytes(16).toString("hex");
  return {
    parameters: {
      algorithm: "SHA-256", cost, keyLength: 32, keyPrefix: "00",
      nonce, salt, expiresAt: Math.floor(Date.now() / 1000) + 300,
    },
    signature: "x",
  };
}

const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

// Warm the JIT so the first real cost is not measured cold.
await solveChallenge({ challenge: mk(100), deriveKey: sha.deriveKey });

console.log("cost\tmedian_ms\tp_max_ms\truns_ms");
for (const cost of COSTS) {
  const samples = [];
  for (let i = 0; i < RUNS; i++) {
    const t0 = Date.now();
    const s = await solveChallenge({ challenge: mk(cost), deriveKey: sha.deriveKey });
    if (!s) { console.log(`cost ${cost}: UNSOLVED`); break; }
    samples.push(Date.now() - t0);
  }
  if (samples.length === RUNS) {
    console.log(`${cost}\t${median(samples)}\t${Math.max(...samples)}\t${samples.join(",")}`);
  }
}
