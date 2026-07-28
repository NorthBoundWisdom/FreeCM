import { defineConfig } from "@vscode/test-cli";
import {
  DEFAULT_VSCODE_TEST_VERSION,
  OFFLINE_VSCODE_LAUNCH_ARGS,
  prepareOfflineVSCodeProfile,
  requireCachedVSCodeRuntime,
} from "./scripts/vscode-test-runtime.mjs";

const vscodeVersion =
  process.env.FREECM_TEST_VSCODE_VERSION ?? DEFAULT_VSCODE_TEST_VERSION;
const vscodeRuntime = requireCachedVSCodeRuntime({ version: vscodeVersion });
const vscodeProfile = await prepareOfflineVSCodeProfile();

export default defineConfig({
  files: "out/test/**/*.test.js",
  useInstallation: {
    fromPath: vscodeRuntime.executablePath,
  },
  launchArgs: [
    // The development extension remains enabled; all unrelated installed or
    // bundled extensions stay out of the offline integration-test profile.
    "--disable-extensions",
    ...OFFLINE_VSCODE_LAUNCH_ARGS,
    `--extensions-dir=${vscodeProfile.extensionsPath}`,
    `--user-data-dir=${vscodeProfile.userDataPath}`,
  ],
  mocha: {
    timeout: 20_000,
  },
});
