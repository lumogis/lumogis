#!/usr/bin/env node
/** Same deps as anthropic spike; placeholder key only. */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../../clients/lumogis-web/node_modules/playwright/index.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SPIKE_HTML = path.join(__dirname, "provider-browser-origin-fetch.html");
const PORT = 9878;

async function main() {
  const server = http.createServer((req, res) => {
    if ((req.url || "").includes("provider-browser-origin-fetch.html") || req.url === "/") {
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
  let out;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${PORT}/provider-browser-origin-fetch.html`, { waitUntil: "networkidle" });
    out = await page.evaluate(async () => {
      const url = "https://api.openai.com/v1/chat/completions";
      const body = JSON.stringify({
        model: "gpt-4o-mini",
        max_tokens: 8,
        messages: [{ role: "user", content: "Say ok." }],
      });
      try {
        const res = await fetch(url, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: "Bearer invalid-placeholder",
          },
          body,
        });
        const text = await res.text();
        return { fetchCompleted: true, httpStatus: res.status, truncatedBody: text.slice(0, 800), threw: false };
      } catch (e) {
        return {
          threw: true,
          errorMessage: String(e.message || e),
        };
      }
    });
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
  console.log(JSON.stringify({ provider: "openai", placeholderOnly: true, origin: `http://127.0.0.1:${PORT}`, openai: out }, null, 2));
}

main();
