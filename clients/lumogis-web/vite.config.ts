// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import {readFileSync} from "node:fs";
import {fileURLToPath} from "node:url";

import react from "@vitejs/plugin-react";
import {defineConfig, loadEnv} from "vite";
import {VitePWA} from "vite-plugin-pwa";

const pkgPath = fileURLToPath(new URL("./package.json", import.meta.url));
const pkgJson = JSON.parse(readFileSync(pkgPath, "utf8")) as {version: string};

// In dev, Vite proxies orchestrator paths to http://localhost:8000 so the SPA
// can reach `/api/v1/*`, `/events`, `/v1/*`, `/mcp`, and legacy root JSON
// routes same-origin without CORS.
// In production, Caddy terminates same-origin routing per docker/caddy/Caddyfile.
export default defineConfig(({mode}) => {
  const env = loadEnv(mode, process.cwd(), "");
  const orchestrator = env.LUMOGIS_DEV_ORCHESTRATOR_URL || "http://localhost:8000";

  return {
    plugins: [
      react(),
      VitePWA({
        strategies: "injectManifest",
        srcDir: "src/pwa",
        filename: "sw.js",
        manifest: false,
        injectRegister: false,
        injectManifest: {
          rollupFormat: "es",
          injectionPoint: "self.__WB_MANIFEST",
          globPatterns: ["**/*.{js,css,html,png,svg,ico,webmanifest,woff2}"],
          globIgnores: ["**/node_modules/**/*", "**/*.map"],
          maximumFileSizeToCacheInBytes: 6 * 1024 * 1024,
        },
      }),
    ],
    define: {
      __LUMOGIS_WEB_PKG_VERSION__: JSON.stringify(pkgJson.version),
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: {
        "/api": {target: orchestrator, changeOrigin: false},
        "/events": {target: orchestrator, changeOrigin: false, ws: false},
        "/v1": {target: orchestrator, changeOrigin: false},
        "/mcp": {target: orchestrator, changeOrigin: false},
        "/search": {
          target: orchestrator,
          changeOrigin: false,
          bypass: (req) => {
            const accept = req.headers.accept ?? "";
            return accept.includes("text/html") ? "/index.html" : undefined;
          },
        },
        "/ingest": {target: orchestrator, changeOrigin: false},
        "/session": {target: orchestrator, changeOrigin: false},
        "/actions": {target: orchestrator, changeOrigin: false},
        "/audit": {target: orchestrator, changeOrigin: false},
        "/routines": {target: orchestrator, changeOrigin: false},
        "/signals": {target: orchestrator, changeOrigin: false},
        "/sources": {target: orchestrator, changeOrigin: false},
        "/feedback": {target: orchestrator, changeOrigin: false},
        "/capabilities": {target: orchestrator, changeOrigin: false},
        "/dashboard": {target: orchestrator, changeOrigin: false},
        "/settings": {target: orchestrator, changeOrigin: false},
        "/graph": {target: orchestrator, changeOrigin: false},
        "/kg": {target: orchestrator, changeOrigin: false},
        "/review-queue": {target: orchestrator, changeOrigin: false},
        "/backup": {target: orchestrator, changeOrigin: false},
        "/restore": {target: orchestrator, changeOrigin: false},
        "/permissions": {target: orchestrator, changeOrigin: false},
        "/browse": {target: orchestrator, changeOrigin: false},
        "/export": {target: orchestrator, changeOrigin: false},
        "/entities": {target: orchestrator, changeOrigin: false},
      },
    },
    build: {
      target: "es2022",
      sourcemap: true,
      outDir: "dist",
    },
  };
});
