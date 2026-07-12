import * as path from "path";

export const DEFAULT_CODE_COUNT_EXCLUDE_PATHS = Object.freeze([
  "build",
  "FreeCM",
  "thirdparty",
  "Downloads",
]);

export const DEFAULT_MAX_FILES = 100_000;
export const DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024;
export const DEFAULT_REPORT_RETENTION = 10;

export function normalizeCodeCountMaxConcurrentReads(
  value: number | null | undefined,
): number | undefined {
  // The legacy integer schema resolved an unset setting to zero.
  return value === null || value === undefined || value === 0 ? undefined : value;
}

export function normalizeCodeCountTarget(
  workspaceRoot: string,
  storedPath: string | undefined,
): string {
  if (storedPath === undefined || storedPath.trim() === "") {
    return normalizePathText(workspaceRoot);
  }
  const workspace = normalizePathText(workspaceRoot);
  const candidate = normalizePathText(storedPath);
  return isPathInside(workspace, candidate) ? candidate : workspace;
}

export function codeCountExcludePathError(value: string): string | undefined {
  const normalized = normalizeCodeCountExcludePathText(value);
  if (normalized.length === 0) {
    return "Exclude path cannot be empty.";
  }
  if (normalized.startsWith("#")) {
    return "Comments are not supported in exclude paths.";
  }
  if (/[*!?]/.test(normalized)) {
    return "Wildcards and negation are not supported in exclude paths.";
  }
  if (path.isAbsolute(normalized) || /^[A-Za-z]:\//.test(normalized)) {
    return "Enter a workspace-relative path, not an absolute path.";
  }
  const trimmed = trimTrailingSlashes(normalized);
  if (trimmed.length === 0) {
    return "Exclude path cannot be empty.";
  }
  const parts = trimmed.split("/");
  if (parts.some((part) => part.length === 0)) {
    return "Exclude path cannot contain empty segments.";
  }
  if (parts.some((part) => part === "." || part === "..")) {
    return "Exclude path cannot contain . or .. segments.";
  }
  return undefined;
}

export function normalizeCodeCountExcludePaths(
  values: readonly string[],
): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    if (codeCountExcludePathError(value) !== undefined) {
      continue;
    }
    const normalized = normalizeCodeCountExcludePath(value);
    const key = normalized.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      result.push(normalized);
    }
  }
  return result;
}

export function parseCodeCountExcludePathsText(value: string): {
  readonly paths: string[];
  readonly error: string | undefined;
} {
  const paths: string[] = [];
  const seen = new Set<string>();
  const lines = value.split(/\r\n|\r|\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.trim().length === 0) {
      continue;
    }
    const error = codeCountExcludePathError(line);
    if (error !== undefined) {
      return { paths: [], error: `Line ${index + 1}: ${error}` };
    }
    const normalized = normalizeCodeCountExcludePath(line);
    const key = normalized.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      paths.push(normalized);
    }
  }
  return { paths, error: undefined };
}

export function isPathInside(parentPath: string, childPath: string): boolean {
  const relative = path.relative(normalizePathText(parentPath), normalizePathText(childPath));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

export function normalizeRelativePath(value: string): string {
  return value.replace(/\\/g, "/").replace(/^\.\//, "");
}

export function normalizeCodeCountExcludePath(value: string): string {
  return trimTrailingSlashes(normalizeCodeCountExcludePathText(value));
}

function normalizeCodeCountExcludePathText(value: string): string {
  return normalizeRelativePath(value.trim());
}

function trimTrailingSlashes(value: string): string {
  return value.replace(/\/+$/, "");
}

function normalizePathText(value: string): string {
  return path.normalize(path.resolve(value));
}
