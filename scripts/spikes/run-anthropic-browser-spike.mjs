#!/usr/bin/env node
/**
 * Automated browser-origin check for Anthropic (non-product).
 * Uses real Chromium via Playwright; qualifies as browser fetch per Chunk 0 bar.
 *
 * Run from repo root:
 *   node scripts/spikes/run-anthropic-browser-spike.mjs
 *
 * Uses only an intentionally invalid placeholder key — no secrets.
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../../clients/lumogis-web/node_modules/playwright/index.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SPIKE_HTML = path.join(__dirname, "provider-browser-origin-fetch.html");

const PORT = Number.parseInt(process.env.PROVIDER_SPIKE_PORT || "9876", 10);

async function main() {
  const server = http.createServer((req, res) => {
    if ((req.url || "").startsWith("/provider-browser-origin-fetch.html") || req.url === "/") {
      fs.readFile(SPIKE_HTML, (err, data) => {
        if (err) {
          res.writeHead(500);
          res.end(String(err));
          return;
        }
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        res.end(data);
      });
      return;
    }
    res.writeHead(404);
    res.end("Not found");
  });

  await new Promise((resolve) => server.listen(PORT, "127.0.0.1", resolve));

  let resultJson;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const origin = `http://127.0.0.1:${PORT}`;
    await page.goto(`${origin}/provider-browser-origin-fetch.html`, {
      waitUntil: "networkidle",
    });

    resultJson = await page.evaluate(async () => {
      const url = "https://api.anthropic.com/v1/messages";
      const body = JSON.stringify({
        model: "claude-3-5-haiku-20241022",
        max_tokens: 8,
        messages: [{ role: "user", content: "Say the word ok." }],
      });
      /** @type Record<string,string> */
      const headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key": "invalid-key-placeholder-spike-only",
        "anthropic-dangerous-direct-browser-access": "true",
      };
      try {
        const res = await fetch(url, { method: "POST", headers, body });
        const text = await res.text();
        return {
          ok: true,
          fetchCompleted: true,
          httpStatus: res.status,
          truncatedBody: text.slice(0, 1500),
          hadCorsOpaqueError: false,
        };
      } catch (e) {
        return {
          ok: false,
          fetchCompleted: false,
          errorName: e && e.name,
          errorMessage: e && e.message,
          hadCorsOpaqueError: String(e && e.message).includes("fetch") || String(e).includes("Failed to fetch"),
        };
      }
    });
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }

  console.log(JSON.stringify({ pageOrigin: `http://127.0.0.1:${PORT}`, provider: "anthropic", result: resultJson }, null, 2));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
