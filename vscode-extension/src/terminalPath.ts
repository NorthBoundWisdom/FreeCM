import * as fs from "fs/promises";
import * as path from "path";
import { parse, ParseError, printParseErrorCode } from "jsonc-parser";
import { ACTIVE_LOCK_NAME, TEMPLATE_LOCK_NAME } from "./lockSchema";

export { ACTIVE_LOCK_NAME, TEMPLATE_LOCK_NAME } from "./lockSchema";

export type TerminalPathPlatform = "linux" | "mac" | "win";

export interface TerminalPathEnvironment {
  readonly env: Record<string, string> | undefined;
  readonly entries: readonly string[];
  readonly lockPath: string | undefined;
}

const TERMINAL_PATH_KEYS = ["common", "linux", "mac", "win"] as const;

export async function terminalPathEnvironmentForRepo(
  repoRoot: string,
  platform: string = process.platform,
  baseEnv: NodeJS.ProcessEnv = process.env,
): Promise<TerminalPathEnvironment> {
  const lock = await loadTerminalPathLock(repoRoot);
  if (lock === undefined) {
    return { env: undefined, entries: [], lockPath: undefined };
  }

  const osGroup = terminalPathPlatformForNodePlatform(platform);
  const entries = terminalPathEntries(lock.data, repoRoot, osGroup, lock.path);
  if (entries.length === 0) {
    return { env: undefined, entries, lockPath: lock.path };
  }

  return {
    env: prependPathEnvironment(entries, platform, baseEnv),
    entries,
    lockPath: lock.path,
  };
}

export function terminalPathPlatformForNodePlatform(
  platform: string,
): TerminalPathPlatform {
  if (platform === "win32") {
    return "win";
  }
  if (platform === "darwin") {
    return "mac";
  }
  return "linux";
}

export function terminalPathEntries(
  lockData: unknown,
  repoRoot: string,
  platform: TerminalPathPlatform,
  pathLabel: string,
): string[] {
  if (!isObject(lockData)) {
    throw new Error(
      `Invalid source-roots lock file (expected object): ${pathLabel}`,
    );
  }
  const terminalPath = lockData.terminalPath ?? {};
  if (!isObject(terminalPath)) {
    throw new Error(`Invalid terminalPath map in ${pathLabel}`);
  }

  const entries = [
    ...terminalPathStringArray(terminalPath, "common", pathLabel),
    ...terminalPathStringArray(terminalPath, platform, pathLabel),
  ];
  return entries.map((entry) => resolveTerminalPathEntry(repoRoot, entry));
}

export function prependPathEnvironment(
  entries: readonly string[],
  platform: string,
  baseEnv: NodeJS.ProcessEnv = process.env,
): Record<string, string> {
  const pathKey = platform === "win32" ? windowsPathKey(baseEnv) : "PATH";
  const delimiter = platform === "win32" ? ";" : ":";
  const existingPath = baseEnv[pathKey] ?? "";
  const nextPath = existingPath
    ? [...entries, existingPath].join(delimiter)
    : entries.join(delimiter);
  return { [pathKey]: nextPath };
}

async function loadTerminalPathLock(
  repoRoot: string,
): Promise<{ path: string; data: unknown } | undefined> {
  for (const lockName of [ACTIVE_LOCK_NAME, TEMPLATE_LOCK_NAME]) {
    const lockPath = path.join(repoRoot, lockName);
    let text: string;
    try {
      text = await fs.readFile(lockPath, "utf8");
    } catch (error) {
      if (isNodeErrorCode(error, "ENOENT")) {
        continue;
      }
      throw new Error(`Unable to read ${lockPath}: ${errorMessage(error)}`);
    }

    const errors: ParseError[] = [];
    const data = parse(text, errors, { allowTrailingComma: true });
    if (errors.length > 0) {
      const details = errors
        .map(
          (error) =>
            `${printParseErrorCode(error.error)} at offset ${error.offset}`,
        )
        .join(", ");
      throw new Error(`Invalid JSONC in ${lockPath}: ${details}`);
    }
    return { path: lockPath, data };
  }
  return undefined;
}

function terminalPathStringArray(
  terminalPath: Record<string, unknown>,
  key: string,
  pathLabel: string,
): string[] {
  const unknownKeys = Object.keys(terminalPath).filter(
    (candidate) =>
      !TERMINAL_PATH_KEYS.includes(
        candidate as (typeof TERMINAL_PATH_KEYS)[number],
      ),
  );
  if (unknownKeys.length > 0) {
    throw new Error(
      `Invalid terminalPath in ${pathLabel}: unexpected keys: ${unknownKeys.join(", ")}`,
    );
  }

  const value = terminalPath[key];
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value)) {
    throw new Error(
      `Invalid terminalPath.${key} in ${pathLabel}; expected string array`,
    );
  }
  return value.map((entry, index) => {
    if (typeof entry !== "string") {
      throw new Error(
        `Invalid terminalPath.${key}[${index}] in ${pathLabel}; expected string`,
      );
    }
    return entry;
  });
}

function resolveTerminalPathEntry(repoRoot: string, entry: string): string {
  if (path.isAbsolute(entry)) {
    return entry;
  }
  return path.resolve(repoRoot, entry);
}

function windowsPathKey(baseEnv: NodeJS.ProcessEnv): string {
  return (
    Object.keys(baseEnv).find((key) => key.toLowerCase() === "path") ?? "Path"
  );
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNodeErrorCode(error: unknown, code: string): boolean {
  return isObject(error) && error.code === code;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
