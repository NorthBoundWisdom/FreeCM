import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import packageJson from "../package.json" with { type: "json" };
import vscePackageJson from "../node_modules/@vscode/vsce/package.json" with { type: "json" };

const scriptDir = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDir, "..");
const repoRoot = resolve(extensionRoot, "..");
const pluginDir = resolve(repoRoot, "plugin");
const platform = `${process.platform}-${process.arch}`;
const outPath = resolve(pluginDir, `RepoMgr_${platform}_v${packageJson.version}.vsix`);

await mkdir(pluginDir, { recursive: true });

const vsceBin = vscePackageJson.bin?.vsce;
if (typeof vsceBin !== "string") {
  throw new Error("@vscode/vsce package does not expose a vsce CLI entrypoint");
}

const vsceCli = resolve(extensionRoot, "node_modules", "@vscode", "vsce", vsceBin);
const result = spawnSync(
  process.execPath,
  [vsceCli, "package", "--allow-missing-repository", "--out", outPath],
  {
    cwd: extensionRoot,
    stdio: "inherit",
  },
);

if (result.error !== undefined) {
  throw result.error;
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

console.log(`Packaged ${outPath}`);
