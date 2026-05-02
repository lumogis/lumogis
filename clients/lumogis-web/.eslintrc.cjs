/* SPDX-License-Identifier: AGPL-3.0-only */
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended",
  ],
  ignorePatterns: ["dist", ".eslintrc.cjs", "src/api/generated/**"],
  parser: "@typescript-eslint/parser",
  plugins: ["react-refresh"],
  rules: {
    "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
  },
  overrides: [
    {
      files: ["src/pwa/sw.ts"],
      rules: {
        /** Service worker entry runs in `ServiceWorkerGlobalScope`; client `tsconfig` libs omit full SW typings. */
        "@typescript-eslint/ban-ts-comment": "off",
      },
    },
  ],
};
