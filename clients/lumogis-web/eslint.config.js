// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig(
  // Parity with ESLint 8 `--ext ts,tsx`: flat config would otherwise also lint *.js/*.mjs/*.cjs
  // (scripts/*.mjs, this file) with no rules. `.tsbuild` holds tsc -b declaration output.
  { ignores: ["dist", ".tsbuild", "src/api/generated/**", "**/*.{js,mjs,cjs}"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.es2022, ...globals.node },
    },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      // Parity with ESLint 8 `eslint:recommended`. ESLint 9 dropped this rule from
      // `recommended` and defaults blockScopedFunctions to "allow"; ESLint 8 flagged them.
      "no-inner-declarations": ["error", "functions", { blockScopedFunctions: "disallow" }],
      // Parity with eslint-plugin-react-hooks@5 `recommended`. The v7 preset adds the
      // React Compiler rules (31 findings in 28 files) — adopting them is a follow-up.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    // shadcn/ui components (LUM-993) are vendored: they export their style variants
    // (buttonVariants, badgeVariants) beside the component, which only costs a full
    // reload instead of a hot update in dev.
    files: ["src/components/ui/**/*.tsx"],
    rules: { "react-refresh/only-export-components": "off" },
  },
  {
    files: ["src/pwa/sw.ts"],
    rules: {
      // Service worker entry runs in ServiceWorkerGlobalScope; client tsconfig libs omit full SW typings.
      "@typescript-eslint/ban-ts-comment": "off",
    },
  },
);
