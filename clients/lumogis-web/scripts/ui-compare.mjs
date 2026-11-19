#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Before/after comparison for a redesign pass (LUM-993). Two commands:
//
//   node scripts/ui-compare.mjs serve [--dist dist] [--port 5199] [--host 127.0.0.1]
//        [--api http://127.0.0.1:8001] [--origin http://lumogis-dev:8080]
//     Serves a built dist/ and passes everything else to a running orchestrator,
//     so the "after" build runs against the same account and data as "before".
//     Page loads (GET with Accept: text/html) get index.html, as Caddy does;
//     files that exist in dist/ are served as they are; any other request goes to
//     --api with its Origin rewritten to --origin (the orchestrator checks it).
//
//   node scripts/ui-compare.mjs page --before DIR --after DIR --out DIR [--title T] [--routes "/a,/b"] [--inline]
//     Joins two folders of ui-compare.spec.ts captures (<mode>__<route>.png)
//     into DIR/index.html with the images copied to DIR/before and DIR/after.
//     --inline embeds the images instead, giving one file that opens anywhere.
//     PNG and JPEG captures are both accepted.
//
// Plain Node, no dependencies.

import fs from "node:fs";
import http from "node:http";
import path from "node:path";

function args(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) out[a.slice(2)] = argv[i + 1]?.startsWith("--") || argv[i + 1] === undefined ? true : argv[++i];
    else out._.push(a);
  }
  return out;
}

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".webmanifest": "application/manifest+json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

function serve(opts) {
  const dist = path.resolve(opts.dist ?? "dist");
  const port = Number(opts.port ?? 5199);
  // Loopback by default: the proxy forwards to the orchestrator with a trusted Origin.
  const host = typeof opts.host === "string" ? opts.host : "127.0.0.1";
  const api = new URL(opts.api ?? "http://127.0.0.1:8001");
  const origin = opts.origin ?? "http://lumogis-dev:8080";
  const index = path.join(dist, "index.html");
  if (!fs.existsSync(index)) throw new Error(`${index} not found; run the web build first`);

  const server = http.createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://local");
    let pathname;
    try {
      pathname = decodeURIComponent(url.pathname);
    } catch {
      res.writeHead(400, { "content-type": "text/plain" });
      res.end("bad path");
      return;
    }
    const file = path.join(dist, pathname);
    const inside = file.startsWith(dist + path.sep);
    if (req.method === "GET" && inside && fs.existsSync(file) && fs.statSync(file).isFile()) {
      res.writeHead(200, { "content-type": TYPES[path.extname(file)] ?? "application/octet-stream" });
      fs.createReadStream(file).pipe(res);
      return;
    }
    if (req.method === "GET" && (req.headers.accept ?? "").includes("text/html")) {
      res.writeHead(200, { "content-type": TYPES[".html"], "cache-control": "no-store" });
      fs.createReadStream(index).pipe(res);
      return;
    }
    const headers = { ...req.headers, host: api.host };
    if (headers.origin) headers.origin = origin;
    if (headers.referer) headers.referer = origin + url.pathname;
    const upstream = http.request(
      { protocol: api.protocol, hostname: api.hostname, port: api.port, method: req.method, path: req.url, headers },
      (up) => {
        res.writeHead(up.statusCode ?? 502, up.headers);
        up.pipe(res);
      },
    );
    upstream.on("error", (err) => {
      res.writeHead(502, { "content-type": "text/plain" });
      res.end(`ui-compare serve: orchestrator unreachable (${err.message})`);
    });
    req.pipe(upstream);
  });
  server.listen(port, host, () => console.log(`ui-compare: ${dist} on http://${host}:${port} (API → ${api.origin})`));
}

/** Same as ui-compare.spec.ts: "/admin/system-status" → "admin.system-status". */
function routeSlug(route) {
  const s = route.trim().replace(/^\/+|\/+$/g, "").replace(/:/g, "").replace(/\//g, ".").replace(/[^a-z0-9.-]+/gi, "-");
  return s.toLowerCase() || "home";
}

const MODE_LABELS = { "desktop-light": "Desktop, light", "desktop-dark": "Desktop, dark", "phone-light": "iPhone 13, light" };
const MODE_ORDER = Object.keys(MODE_LABELS);

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/** Groups `<mode>__<route>.png` names by route, in first-seen order. */
export function groupCaptures(names) {
  const routes = new Map();
  for (const name of names) {
    const m = /^([a-z-]+)__([a-z0-9.-]+)\.(?:png|jpe?g)$/.exec(name);
    if (!m) continue;
    const [, mode, route] = m;
    if (!routes.has(route)) routes.set(route, new Set());
    routes.get(route).add(mode);
  }
  return routes;
}

function page(opts) {
  for (const k of ["before", "after", "out"]) if (!opts[k]) throw new Error(`--${k} is required`);
  const out = path.resolve(opts.out);
  const inline = opts.inline === true;
  const list = (dir) => (fs.existsSync(dir) ? fs.readdirSync(dir).filter((f) => /\.(png|jpe?g)$/.test(f)).sort() : []);
  // capture key ("desktop-light__chat") → file name, per side
  const index = (dir) => new Map(list(dir).map((f) => [f.replace(/\.[a-z]+$/, ""), f]));
  const before = index(opts.before);
  const after = index(opts.after);
  fs.mkdirSync(out, { recursive: true });
  if (!inline) {
    for (const [side, dir, names] of [["before", opts.before, before], ["after", opts.after, after]]) {
      fs.mkdirSync(path.join(out, side), { recursive: true });
      for (const n of names.values()) fs.copyFileSync(path.join(dir, n), path.join(out, side, n));
    }
  }
  const src = (side, file) => {
    const dir = side === "before" ? opts.before : opts.after;
    if (!inline) return `${side}/${encodeURIComponent(file)}`;
    const type = file.endsWith(".png") ? "image/png" : "image/jpeg";
    return `data:${type};base64,${fs.readFileSync(path.join(dir, file)).toString("base64")}`;
  };
  const grouped = groupCaptures([...after.values(), ...before.values()]);
  // --routes "/chat,/search,…" (the spec's UI_COMPARE_ROUTES) keeps that order;
  // anything not listed follows alphabetically.
  const wanted = typeof opts.routes === "string" ? opts.routes.split(",").map(routeSlug) : [];
  const rank = (r) => (wanted.includes(r) ? wanted.indexOf(r) : wanted.length);
  const routes = new Map([...grouped.entries()].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b)));
  const title = opts.title === true || !opts.title ? "Before and after" : opts.title;

  const cell = (side, key) => {
    const file = (side === "before" ? before : after).get(key);
    if (!file) return `<div class="missing">Not captured</div>`;
    const img = `<img loading="lazy" src="${src(side, file)}" alt="${side}">`;
    return inline ? img : `<a href="${side}/${encodeURIComponent(file)}" target="_blank">${img}</a>`;
  };

  const sections = [...routes.entries()]
    .map(([route, modes]) => {
      const rows = MODE_ORDER.filter((m) => modes.has(m))
        .map((m) => {
          const file = `${m}__${route}`;
          return `<div class="row ${m.startsWith("phone") ? "phone" : ""}"><h3>${escapeHtml(MODE_LABELS[m] ?? m)}</h3><div class="pair"><figure><figcaption>Before</figcaption>${cell("before", file)}</figure><figure><figcaption>After</figcaption>${cell("after", file)}</figure></div></div>`;
        })
        .join("");
      return `<section id="${escapeHtml(route)}"><h2>/${escapeHtml(route.replace(/\./g, "/"))}</h2>${rows}</section>`;
    })
    .join("\n");
  const nav = [...routes.keys()].map((r) => `<a href="#${escapeHtml(r)}">/${escapeHtml(r.replace(/\./g, "/"))}</a>`).join("");

  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>
:root{--bg:#faf7f1;--fg:#1a1510;--muted:#6c6358;--card:#fff;--line:#d6ccbb;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#0d0d0f;--fg:#f4f1ea;--muted:#a59c8c;--card:#17171b;--line:#2e2e36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:1;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px}
h1{margin:0 0 6px;font-size:20px}nav{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:13px}nav a{color:var(--muted)}
main{padding:16px;max-width:1800px;margin:0 auto}section{margin:0 0 40px}h2{font-size:18px;margin:24px 0 8px}
h3{font-size:13px;font-weight:600;color:var(--muted);margin:12px 0 6px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}.phone .pair{grid-template-columns:repeat(2,minmax(0,320px))}
figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
figcaption{font-size:12px;font-weight:600;padding:6px 10px;border-bottom:1px solid var(--line)}
img{display:block;width:100%;height:auto}.missing{padding:40px 10px;text-align:center;color:var(--muted)}
@media (max-width:700px){.pair,.phone .pair{grid-template-columns:1fr}}
</style></head><body>
<header><h1>${escapeHtml(title)}</h1><nav>${nav}</nav></header>
<main>${sections || "<p>No captures found.</p>"}</main>
</body></html>
`;
  fs.writeFileSync(path.join(out, "index.html"), html);
  console.log(`ui-compare: ${routes.size} routes → ${path.join(out, "index.html")}`);
}

const opts = args(process.argv.slice(2));
const cmd = opts._[0];
if (cmd === "serve") serve(opts);
else if (cmd === "page") page(opts);
else {
  console.error("usage: ui-compare.mjs serve|page [options] (see the header of this file)");
  process.exit(2);
}
