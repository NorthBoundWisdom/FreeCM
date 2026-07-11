import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path, { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  downloadAndUnzipVSCode,
  resolveCliArgsFromVSCodeExecutablePath,
  runTests,
} from "@vscode/test-electron";
import JSZip from "jszip";
import packageJson from "../package.json" with { type: "json" };

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDirectory, "..");
const repoRoot = resolve(extensionRoot, "..");
export const MAX_VSIX_COMPRESSED_BYTES = 750 * 1024;
export const MAX_VSIX_UNPACKED_BYTES = 1024 * 1024;
export const MAX_ICON_BYTES = 100 * 1024;

const allowedExactFiles = new Set([
  "[Content_Types].xml",
  "extension.vsixmanifest",
  "extension/LICENSE.txt",
  "extension/package.json",
  "extension/readme.md",
  "extension/resources/freecm.svg",
  "extension/resources/freecm-icon.png",
  "extension/resources/workflow.css",
  "extension/resources/workflow.js",
  "extension/node_modules/jsonc-parser/package.json",
  "extension/node_modules/jsonc-parser/LICENSE.md",
  "extension/node_modules/ignore/package.json",
  "extension/node_modules/ignore/index.js",
  "extension/node_modules/ignore/LICENSE-MIT",
]);
const allowedPrefixes = [
  "extension/out/",
  "extension/node_modules/jsonc-parser/",
  "extension/node_modules/ignore/",
];

function isAllowedArchivePath(archivePath, directory) {
  if (directory) {
    return (
      [...allowedExactFiles].some((filePath) => filePath.startsWith(archivePath)) ||
      allowedPrefixes.some(
        (prefix) => prefix.startsWith(archivePath) || archivePath.startsWith(prefix),
      )
    );
  }
  if (allowedExactFiles.has(archivePath)) {
    return true;
  }
  if (archivePath.startsWith("extension/out/")) {
    return archivePath.endsWith(".js");
  }
  return [
    "extension/node_modules/jsonc-parser/",
    "extension/node_modules/ignore/",
  ].some((prefix) => archivePath.startsWith(prefix));
}

function requireArchiveFile(archive, fileName) {
  const entry = archive.file(fileName);
  if (entry === null) {
    throw new Error(`VSIX is missing required file: ${fileName}`);
  }
  return entry;
}

export async function inspectVsix(
  vsixPath,
  expectedPackage = packageJson,
  expectedVersion = packageJson.version,
) {
  const vsixBytes = await readFile(vsixPath);
  assert.ok(
    vsixBytes.length <= MAX_VSIX_COMPRESSED_BYTES,
    `VSIX compressed size ${vsixBytes.length} exceeds ${MAX_VSIX_COMPRESSED_BYTES} bytes`,
  );
  const archive = await JSZip.loadAsync(vsixBytes, {
    checkCRC32: true,
    createFolders: true,
  });
  const caseFoldedPaths = new Set();
  for (const [archivePath, entry] of Object.entries(archive.files)) {
    const originalPath = entry.unsafeOriginalName ?? archivePath;
    const pathParts = originalPath.replaceAll("\\", "/").split("/");
    if (
      originalPath !== archivePath ||
      originalPath.includes("\\") ||
      originalPath.startsWith("/") ||
      /^[A-Za-z]:/.test(originalPath) ||
      pathParts.includes("..")
    ) {
      throw new Error(`VSIX contains unsafe archive path: ${originalPath}`);
    }
    const caseFoldedPath = archivePath.toLowerCase();
    if (caseFoldedPaths.has(caseFoldedPath)) {
      throw new Error(`VSIX contains a duplicate archive path: ${archivePath}`);
    }
    caseFoldedPaths.add(caseFoldedPath);
    assert.ok(
      isAllowedArchivePath(archivePath, entry.dir),
      `VSIX contains file outside the archive allowlist: ${archivePath}`,
    );
  }
  const unpackedBytes = (
    await Promise.all(
      Object.values(archive.files)
        .filter((entry) => !entry.dir)
        .map(async (entry) => (await entry.async("uint8array")).length),
    )
  ).reduce((total, size) => total + size, 0);
  assert.ok(
    unpackedBytes <= MAX_VSIX_UNPACKED_BYTES,
    `VSIX unpacked size ${unpackedBytes} exceeds ${MAX_VSIX_UNPACKED_BYTES} bytes`,
  );

  const packagedManifest = JSON.parse(
    await requireArchiveFile(archive, "extension/package.json").async("string"),
  );
  for (const field of ["name", "publisher", "main"]) {
    assert.equal(
      packagedManifest[field],
      expectedPackage[field],
      `VSIX package.json ${field} mismatch`,
    );
  }
  assert.equal(packagedManifest.version, expectedVersion, "VSIX package.json version mismatch");
  assert.equal(
    packagedManifest.engines?.vscode,
    expectedPackage.engines?.vscode,
    "VSIX package.json VS Code engine mismatch",
  );

  const vsixManifest = await requireArchiveFile(archive, "extension.vsixmanifest").async(
    "string",
  );
  for (const fragment of [
    `Id="${expectedPackage.name}"`,
    `Version="${expectedVersion}"`,
    `Publisher="${expectedPackage.publisher}"`,
    `Microsoft.VisualStudio.Code.Engine" Value="${expectedPackage.engines.vscode}"`,
  ]) {
    assert.ok(vsixManifest.includes(fragment), `VSIX manifest is missing ${fragment}`);
  }

  for (const requiredPath of [
    "[Content_Types].xml",
    "extension/out/extension.js",
    "extension/resources/freecm.svg",
    "extension/resources/freecm-icon.png",
    "extension/resources/workflow.css",
    "extension/resources/workflow.js",
    "extension/node_modules/jsonc-parser/package.json",
    "extension/node_modules/ignore/package.json",
    "extension/node_modules/ignore/index.js",
  ]) {
    requireArchiveFile(archive, requiredPath);
  }
  const iconBytes = await requireArchiveFile(
    archive,
    "extension/resources/freecm-icon.png",
  ).async("nodebuffer");
  assert.ok(iconBytes.length <= MAX_ICON_BYTES, "VSIX icon exceeds the size budget");
  assert.equal(iconBytes.subarray(1, 4).toString("ascii"), "PNG", "VSIX icon is not PNG");
  assert.ok(
    iconBytes.readUInt32BE(16) <= 256 && iconBytes.readUInt32BE(20) <= 256,
    "VSIX icon dimensions exceed 256x256",
  );

  const packagedPaths = Object.keys(archive.files);
  for (const forbiddenPrefix of [
    "extension/src/",
    "extension/scripts/",
    "extension/out/test/",
    "extension/test/",
  ]) {
    assert.ok(
      !packagedPaths.some((archivePath) => archivePath.startsWith(forbiddenPrefix)),
      `VSIX contains forbidden development path: ${forbiddenPrefix}`,
    );
  }
  assert.ok(
    !packagedPaths.some((archivePath) => archivePath.endsWith(".map")),
    "VSIX contains source maps",
  );
  for (const archivePath of packagedPaths.filter(
    (name) => name.startsWith("extension/node_modules/") && !archive.files[name].dir,
  )) {
    assert.ok(
      archivePath.startsWith("extension/node_modules/jsonc-parser/") ||
        archivePath.startsWith("extension/node_modules/ignore/"),
      `VSIX contains unexpected runtime dependency: ${archivePath}`,
    );
  }
  return packagedManifest;
}

function runCli(cli, args) {
  const shell = process.platform === "win32";
  const completed = spawnSync(shell ? `"${cli}"` : cli, args, {
    encoding: "utf8",
    shell,
    timeout: 120_000,
  });
  if (completed.error !== undefined) {
    throw completed.error;
  }
  if (completed.status !== 0) {
    throw new Error(
      `VS Code CLI failed (${completed.status}): ${args.join(" ")}\n` +
        `${completed.stdout ?? ""}${completed.stderr ?? ""}`,
    );
  }
  return `${completed.stdout ?? ""}${completed.stderr ?? ""}`;
}

async function writeHarness(harnessRoot) {
  await mkdir(harnessRoot, { recursive: true });
  await writeFile(
    path.join(harnessRoot, "package.json"),
    `${JSON.stringify(
      {
        name: "freecm-vsix-smoke-harness",
        publisher: "freecm-tests",
        version: "0.0.0",
        engines: { vscode: "^1.90.0" },
        main: "./extension.cjs",
        activationEvents: [],
      },
      null,
      2,
    )}\n`,
    "utf8",
  );
  await writeFile(
    path.join(harnessRoot, "extension.cjs"),
    "exports.activate = function activate() {};\n",
    "utf8",
  );
}

export async function smokeVsix() {
  const versionPath = resolve(repoRoot, "VERSION");
  const repoVersion = (await readFile(versionPath, "utf8")).trim();
  assert.equal(packageJson.version, repoVersion, "package.json and VERSION differ");
  const platform = `${process.platform}-${process.arch}`;
  const vsixPath = resolve(repoRoot, "plugin", `FreeCM_${platform}_v${repoVersion}.vsix`);
  await inspectVsix(vsixPath, packageJson, repoVersion);

  const tempRoot = await mkdtemp(path.join(tmpdir(), "freecm-vsix-smoke-"));
  const extensionsDir = path.join(tempRoot, "extensions");
  const userDataDir = path.join(tempRoot, "user-data");
  const workspaceDir = path.join(tempRoot, "workspace");
  const harnessRoot = path.join(tempRoot, "harness");
  await Promise.all([
    mkdir(extensionsDir),
    mkdir(userDataDir),
    mkdir(workspaceDir),
    writeHarness(harnessRoot),
  ]);
  try {
    const vscodeVersion = process.env.FREECM_SMOKE_VSCODE_VERSION ?? "stable";
    const vscodeExecutablePath = await downloadAndUnzipVSCode(vscodeVersion);
    const [cli, ...defaultCliArgs] = resolveCliArgsFromVSCodeExecutablePath(
      vscodeExecutablePath,
      { reuseMachineInstall: true },
    );
    const profileArgs = [
      `--extensions-dir=${extensionsDir}`,
      `--user-data-dir=${userDataDir}`,
    ];
    runCli(cli, [
      ...defaultCliArgs,
      ...profileArgs,
      "--install-extension",
      vsixPath,
      "--force",
    ]);
    const installed = runCli(cli, [
      ...defaultCliArgs,
      ...profileArgs,
      "--list-extensions",
      "--show-versions",
    ]);
    assert.ok(
      installed
        .split(/\r?\n/)
        .map((line) => line.trim().toLowerCase())
        .includes(`ethan-kang.freecm@${repoVersion}`),
      `installed extension list did not contain ethan-kang.freecm@${repoVersion}`,
    );

    const exitCode = await runTests({
      vscodeExecutablePath,
      reuseMachineInstall: true,
      extensionDevelopmentPath: harnessRoot,
      extensionTestsPath: resolve(scriptDirectory, "vsix-smoke-runner.cjs"),
      launchArgs: [
        workspaceDir,
        ...profileArgs,
        "--disable-extension-auto-update",
        "--disable-telemetry",
      ],
      extensionTestsEnv: {
        FREECM_SMOKE_CHECKOUT_ROOT: await realpath(repoRoot),
        FREECM_SMOKE_EXPECTED_VERSION: repoVersion,
        FREECM_SMOKE_EXTENSIONS_DIR: await realpath(extensionsDir),
        FREECM_SMOKE_HARNESS_ROOT: await realpath(harnessRoot),
      },
    });
    assert.equal(exitCode, 0, `installed VSIX activation exited with ${exitCode}`);
    console.log(`Installed VSIX smoke passed: ${vsixPath}`);
  } finally {
    await rm(tempRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (invokedPath === import.meta.url) {
  try {
    await smokeVsix();
  } catch (error) {
    console.error(error instanceof Error ? error.stack : error);
    process.exitCode = 1;
  }
}
