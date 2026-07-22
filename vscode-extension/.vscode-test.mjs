import { defineConfig } from "@vscode/test-cli";

export default defineConfig({
  files: "out/test/**/*.test.js",
  // Keep regression checks reproducible; upgrade this intentionally after
  // validating the extension against a newer VS Code release.
  version: process.env.FREECM_TEST_VSCODE_VERSION ?? "1.129.1",
  mocha: {
    timeout: 20_000,
  },
});
