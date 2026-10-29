import js from "@eslint/js";
import tsParser from "@typescript-eslint/parser";

export default [
  js.configs.recommended,
  {
    ignores: ["dist/**", "node_modules/**"],
  },
  {
    files: ["src/**/*.{js,jsx,ts,tsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      parser: tsParser,
      globals: {
        document: "readonly",
        describe: "readonly",
        expect: "readonly",
        AbortController: "readonly",
        AbortSignal: "readonly",
        Blob: "readonly",
        fetch: "readonly",
        FormData: "readonly",
        File: "readonly",
        HTMLDivElement: "readonly",
        HTMLFormElement: "readonly",
        HTMLElement: "readonly",
        it: "readonly",
        React: "readonly",
        RequestInfo: "readonly",
        RequestInit: "readonly",
        Response: "readonly",
        Storage: "readonly",
        TextEncoder: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        window: "readonly",
      },
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
        sourceType: "module",
      },
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
];
