import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import packageJson from "../package.json" with { type: "json" };

const scriptDir = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDir, "..");
const sourcePath = resolve(extensionRoot, "src", "buildInfo.ts");

function pad(value) {
  return String(value).padStart(2, "0");
}

function formatLocalDateTime(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

await mkdir(resolve(extensionRoot, "src"), { recursive: true });
await writeFile(
  sourcePath,
  `export const EXTENSION_BUILD_INFO = {\n` +
    `  version: ${JSON.stringify(packageJson.version)},\n` +
    `  compiledAt: ${JSON.stringify(formatLocalDateTime(new Date()))},\n` +
    `} as const;\n`,
  "utf8",
);

