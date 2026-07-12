// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { initTheme } from "./design/theme";
import { initSidebarCollapsed } from "./design/sidebarCollapse";
import { registerLumogisServiceWorker } from "./pwa/registerServiceWorker";
import "./design/tokens.css";

initTheme();
initSidebarCollapsed();
registerLumogisServiceWorker();

const container = document.getElementById("root");
if (!container) throw new Error("Lumogis Web: missing #root element in index.html");

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
