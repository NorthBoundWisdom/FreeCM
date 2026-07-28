import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { downloadAndUnzipVSCode } from "@vscode/test-electron";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDirectory, "..");

export const DEFAULT_VSCODE_TEST_VERSION = "1.129.1";
export const DEFAULT_VSCODE_TEST_CACHE = resolve(extensionRoot, ".vscode-test");
export const DEFAULT_VSCODE_TEST_PROFILE = resolve(
  DEFAULT_VSCODE_TEST_CACHE,
  "offline-profile",
);
export const OFFLINE_VSCODE_SETTINGS = {
  "chat.disableAIFeatures": true,
  "extensions.autoCheckUpdates": false,
  "extensions.autoUpdate": false,
  "extensions.gallery.serviceUrl": "https://offline.invalid",
  "telemetry.telemetryLevel": "off",
  "update.mode": "none",
  "workbench.enableExperiments": false,
};
export const OFFLINE_VSCODE_LAUNCH_ARGS = [
  "--disable-experiments",
  "--disable-telemetry",
  "--proxy-server=http://127.0.0.1:9",
  "--proxy-bypass-list=localhost;127.0.0.1;[::1]",
];

export function vscodeTestPlatform(
  operatingSystem = process.platform,
  architecture = process.arch,
) {
  if (operatingSystem === "darwin") {
    return architecture === "arm64" ? "darwin-arm64" : "darwin";
  }
  if (operatingSystem === "win32") {
    return architecture === "arm64"
      ? "win32-arm64-archive"
      : "win32-x64-archive";
  }
  if (operatingSystem === "linux") {
    if (architecture === "arm64") {
      return "linux-arm64";
    }
    if (architecture === "arm") {
      return "linux-armhf";
    }
    return "linux-x64";
  }
  throw new Error(
    `Unsupported VS Code test platform: ${operatingSystem}-${architecture}`,
  );
}

export function cachedVSCodeRuntime(options = {}) {
  const version = options.version ?? DEFAULT_VSCODE_TEST_VERSION;
  const platform = options.platform ?? vscodeTestPlatform();
  const cachePath = options.cachePath ?? DEFAULT_VSCODE_TEST_CACHE;
  validateVersion(version);

  const installPath = resolve(cachePath, `vscode-${platform}-${version}`);
  const executablePath = platform.includes("win32")
    ? resolve(installPath, "Code.exe")
    : platform.includes("darwin")
      ? resolve(
          installPath,
          "Visual Studio Code.app",
          "Contents",
          "MacOS",
          "Electron",
        )
      : resolve(installPath, "code");
  return {
    version,
    platform,
    cachePath,
    installPath,
    executablePath,
    completionPath: resolve(installPath, "is-complete"),
  };
}

export function requireCachedVSCodeRuntime(options = {}) {
  const runtime = cachedVSCodeRuntime(options);
  if (
    !existsSync(runtime.completionPath) ||
    !existsSync(runtime.executablePath)
  ) {
    throw new Error(
      `VS Code test runtime ${runtime.version} (${runtime.platform}) is not prepared. ` +
        "Run `npm run prepare:test-runtime` while network access is available. " +
        `Expected ${runtime.executablePath}`,
    );
  }
  return runtime;
}

export async function prepareOfflineVSCodeProfile(options = {}) {
  const profilePath = options.profilePath ?? DEFAULT_VSCODE_TEST_PROFILE;
  const userDataPath = resolve(profilePath, "user-data");
  const extensionsPath = resolve(profilePath, "extensions");
  const userSettingsPath = resolve(userDataPath, "User", "settings.json");
  await Promise.all([
    mkdir(resolve(userDataPath, "User"), { recursive: true }),
    mkdir(extensionsPath, { recursive: true }),
  ]);
  await writeFile(
    userSettingsPath,
    `${JSON.stringify(OFFLINE_VSCODE_SETTINGS, null, 2)}\n`,
    "utf8",
  );
  return {
    profilePath,
    userDataPath,
    extensionsPath,
    userSettingsPath,
  };
}

export async function prepareVSCodeTestRuntime(options = {}) {
  const runtime = cachedVSCodeRuntime(options);
  await downloadAndUnzipVSCode({
    version: runtime.version,
    platform: runtime.platform,
    cachePath: runtime.cachePath,
  });
  const prepared = requireCachedVSCodeRuntime(options);
  console.log(`Prepared VS Code test runtime: ${prepared.executablePath}`);
  return prepared;
}

function validateVersion(version) {
  if (!/^\d+\.\d+\.\d+$/.test(version)) {
    throw new Error(`VS Code test version must be an exact release: ${version}`);
  }
}

const invokedPath = process.argv[1]
  ? pathToFileURL(resolve(process.argv[1])).href
  : "";
if (invokedPath === import.meta.url) {
  try {
    await prepareVSCodeTestRuntime({
      version:
        process.env.FREECM_TEST_VSCODE_VERSION ??
        DEFAULT_VSCODE_TEST_VERSION,
    });
  } catch (error) {
    console.error(error instanceof Error ? error.stack : error);
    process.exitCode = 1;
  }
}
