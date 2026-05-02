#!/usr/bin/env node
/**
 * Serves static files for browser-origin provider spike only.
 * Not imported by lumogis-web production bundles.
 *
 * Usage: node scripts/spikes/serve-provider-spike.mjs [port]
 * Default port: 9876
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = __dirname;

const port = Number.parseInt(process.argv[2] || "9876", 10);

const mime = {
  ".html": "text/html; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
};

const server = http.createServer((req, res) => {
  const url = new URL(req.url || "/", `http://127.0.0.1:${port}`);
  let p = path.normalize(url.pathname).replace(/^(\.\.(\/|\\|$))+/, "");
  if (p === "/" || p === "") p = "/provider-browser-origin-fetch.html";
  const filePath = path.join(ROOT, p);
  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    const ext = path.extname(filePath);
    res.writeHead(200, { "Content-Type": mime[ext] || "application/octet-stream" });
    res.end(data);
  });
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Provider spike static server: http://127.0.0.1:${port}/provider-browser-origin-fetch.html`);
});
