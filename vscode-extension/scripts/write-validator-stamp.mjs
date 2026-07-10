import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const defaultExtensionRoot = path.resolve(scriptDirectory, "..");

function parseExtensionRoot(args) {
  if (args.length === 0) {
    return defaultExtensionRoot;
  }
  if (args.length === 2 && args[0] === "--extension-root") {
    return path.resolve(args[1]);
  }
  throw new Error(`Usage: node write-validator-stamp.mjs [--extension-root <path>]`);
}

function validatePaths(value, fieldName) {
  if (
    !Array.isArray(value) ||
    value.length === 0 ||
    value.some((item) => typeof item !== "string" || item.length === 0) ||
    new Set(value).size !== value.length
  ) {
    throw new Error(`${fieldName} must be a non-empty array of unique paths`);
  }
  return value;
}

function resolvedContractPath(extensionRoot, relativePath) {
  const normalized = relativePath.replaceAll("\\", "/");
  if (normalized.startsWith("/") || normalized.split("/").includes("..")) {
    throw new Error(`Unsafe validator build path: ${relativePath}`);
  }
  const resolved = path.resolve(extensionRoot, ...normalized.split("/"));
  const relative = path.relative(extensionRoot, resolved);
  if (relative === "" || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Unsafe validator build path: ${relativePath}`);
  }
  return resolved;
}

function sha256(filePath) {
  return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function hashes(extensionRoot, relativePaths) {
  return Object.fromEntries(
    [...relativePaths]
      .sort()
      .map((relativePath) => [
        relativePath,
        sha256(resolvedContractPath(extensionRoot, relativePath)),
      ]),
  );
}

const extensionRoot = parseExtensionRoot(process.argv.slice(2));
const contractPath = path.join(extensionRoot, "validator-build-contract.json");
const contract = JSON.parse(fs.readFileSync(contractPath, "utf8"));
if (contract.schemaVersion !== 1 || contract.algorithm !== "sha256") {
  throw new Error("Unsupported validator build contract");
}
const inputs = validatePaths(contract.inputs, "inputs");
const outputs = validatePaths(contract.outputs, "outputs");
const stampPath = resolvedContractPath(extensionRoot, contract.stampPath);
const stamp = {
  schemaVersion: contract.schemaVersion,
  algorithm: contract.algorithm,
  inputs: hashes(extensionRoot, inputs),
  outputs: hashes(extensionRoot, outputs),
};
const temporaryPath = `${stampPath}.${process.pid}.${Date.now()}.tmp`;
fs.mkdirSync(path.dirname(stampPath), { recursive: true });
try {
  fs.writeFileSync(temporaryPath, `${JSON.stringify(stamp, null, 2)}\n`, {
    encoding: "utf8",
    flag: "wx",
  });
  fs.renameSync(temporaryPath, stampPath);
} catch (error) {
  fs.rmSync(temporaryPath, { force: true });
  throw error;
}
