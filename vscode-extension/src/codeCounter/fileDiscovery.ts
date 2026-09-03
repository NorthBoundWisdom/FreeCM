import * as fs from "fs/promises";
import { Dirent } from "fs";
import * as path from "path";
import * as vscode from "vscode";
import ignore = require("ignore");
import { beginFilesystemRead } from "../performanceMetrics";
import { LineCounter, LineCounterTable } from "./lineCounter";
import {
  isPathInside,
  normalizeCodeCountExcludePath,
  normalizeRelativePath,
} from "./settings";

export interface SourceCandidate {
  readonly uri: vscode.Uri;
  readonly counter: LineCounter;
}

interface ScopedIgnore {
  readonly basePath: string;
  readonly matcher: ignore.Ignore;
}

export const EXCLUDED_CODE_COUNT_FORMATS = Object.freeze([
  "Batch (.bat, .cmd)", "CSS/styles (.css, .scss, .sass, .less)",
  "HTML (.html, .htm)",
  "Ignore files (.gitignore, .ignore, .dockerignore, .eslintignore, .npmignore)",
  "INI/config/properties (.ini, .cfg, .conf, .config, .properties, .toml)",
  "JSON (.json, .jsonc, .json5, tsconfig.json, jsconfig.json)",
  "Markdown (.md, .markdown)", "pip requirements (requirements*.txt, Pipfile)",
  "reStructuredText (.rst)", "XML (.xml, .xib, .storyboard, .plist, .svg)",
  "YAML (.yaml, .yml)",
]);

const EXCLUDED_EXTENSIONS = new Set([
  ".bat", ".cfg", ".cmd", ".conf", ".config", ".css", ".gitignore",
  ".htm", ".html", ".ignore", ".ini", ".json", ".json5", ".jsonc",
  ".less", ".markdown", ".md", ".plist", ".properties", ".rst", ".sass",
  ".scss", ".storyboard", ".svg", ".toml", ".xib", ".xml", ".yaml", ".yml",
]);
const EXCLUDED_FILENAMES = new Set([
  ".dockerignore", ".eslintignore", ".gitignore", ".npmignore", "dockerignore",
  "eslintignore", "gitignore", "jsconfig.json", "npmignore", "pipfile",
  "requirements-dev.txt", "requirements-test.txt", "requirements.txt", "tsconfig.json",
]);
const EXCLUDED_LANGUAGES = new Set([
  "batch", "css", "ignore", "json", "json with comments", "less",
  "pip requirements", "properties", "restructuredtext", "sass", "scss",
]);
const INTERNAL_EXCLUDES = [".git", ".freecm/counts"];

export async function discoverSourceCandidates(input: {
  readonly workspaceRoot: string;
  readonly targetPath: string;
  readonly outputRoot: string;
  readonly table: LineCounterTable;
  readonly excludePaths: readonly string[];
  readonly maxFiles: number;
  readonly cancellationToken?: vscode.CancellationToken;
}): Promise<SourceCandidate[]> {
  throwIfCancelled(input.cancellationToken);
  const finish = beginFilesystemRead();
  try {
    const excludePaths = [...INTERNAL_EXCLUDES, ...input.excludePaths];
    const matchers = await loadAncestorIgnoreMatchers(
      input.workspaceRoot,
      input.targetPath,
      input.cancellationToken,
    );
    const candidates: SourceCandidate[] = [];
    await walkDirectory({
      directory: path.resolve(input.targetPath),
      workspaceRoot: path.resolve(input.workspaceRoot),
      targetPath: path.resolve(input.targetPath),
      outputRoot: path.resolve(input.outputRoot),
      table: input.table,
      excludePaths,
      maxFiles: input.maxFiles,
      cancellationToken: input.cancellationToken,
      matchers,
      candidates,
    });
    return candidates;
  } finally {
    finish();
  }
}

async function walkDirectory(state: {
  readonly directory: string;
  readonly workspaceRoot: string;
  readonly targetPath: string;
  readonly outputRoot: string;
  readonly table: LineCounterTable;
  readonly excludePaths: readonly string[];
  readonly maxFiles: number;
  readonly cancellationToken?: vscode.CancellationToken;
  readonly matchers: readonly ScopedIgnore[];
  readonly candidates: SourceCandidate[];
}): Promise<void> {
  throwIfCancelled(state.cancellationToken);
  if (shouldSkipDirectory(state.directory, state)) {
    return;
  }

  let entries: Dirent[];
  try {
    entries = await fs.readdir(state.directory, { withFileTypes: true });
  } catch {
    return;
  }

  const matchers = await matchersWithLocalGitignore(
    state.directory,
    entries,
    state.matchers,
  );

  for (const entry of entries) {
    throwIfCancelled(state.cancellationToken);
    // Following directory symlinks can escape the workspace, including CPack
    // DragNDrop links such as Applications -> /Applications under build/.
    if (entry.isSymbolicLink()) {
      continue;
    }
    const fullPath = path.join(state.directory, entry.name);
    if (entry.isDirectory()) {
      await walkDirectory({ ...state, directory: fullPath, matchers });
      continue;
    }
    if (!entry.isFile()) {
      continue;
    }
    const counter = state.table.getCounter(fullPath);
    if (
      counter === undefined ||
      isExcludedFile(fullPath) ||
      EXCLUDED_LANGUAGES.has(counter.name.toLowerCase()) ||
      !isPathInside(state.targetPath, fullPath) ||
      isPathInside(state.outputRoot, fullPath) ||
      isExcludedPath(state.workspaceRoot, fullPath, state.excludePaths) ||
      isIgnored(fullPath, matchers)
    ) {
      continue;
    }
    state.candidates.push({ uri: vscode.Uri.file(fullPath), counter });
    if (state.candidates.length > state.maxFiles) {
      throw new Error(
        `Code count found more than maxFiles=${state.maxFiles} supported source files. Narrow the target or increase freecm.codeCount.maxFiles.`,
      );
    }
  }
}

function shouldSkipDirectory(
  directory: string,
  state: {
    readonly workspaceRoot: string;
    readonly outputRoot: string;
    readonly excludePaths: readonly string[];
    readonly matchers: readonly ScopedIgnore[];
  },
): boolean {
  return isPathInside(state.outputRoot, directory)
    || isExcludedPath(state.workspaceRoot, directory, state.excludePaths)
    || isIgnored(directory, state.matchers, true);
}

async function loadAncestorIgnoreMatchers(
  workspaceRoot: string,
  targetPath: string,
  token: vscode.CancellationToken | undefined,
): Promise<ScopedIgnore[]> {
  const paths: string[] = [];
  const workspace = path.resolve(workspaceRoot);
  const target = path.resolve(targetPath);
  let directory = path.dirname(target);
  while (isPathInside(workspace, directory) && directory !== target) {
    paths.push(path.join(directory, ".gitignore"));
    if (directory === workspace) {
      break;
    }
    const parent = path.dirname(directory);
    if (parent === directory) {
      break;
    }
    directory = parent;
  }
  const matchers: ScopedIgnore[] = [];
  for (const ignorePath of paths.sort((left, right) => left.length - right.length)) {
    throwIfCancelled(token);
    if (isIgnored(ignorePath, matchers)) {
      continue;
    }
    const matcher = await readIgnoreMatcher(ignorePath);
    if (matcher !== undefined) {
      matchers.push(matcher);
    }
  }
  return matchers;
}

async function matchersWithLocalGitignore(
  directory: string,
  entries: readonly Dirent[],
  matchers: readonly ScopedIgnore[],
): Promise<readonly ScopedIgnore[]> {
  const hasGitignore = entries.some(
    (entry) =>
      entry.name === ".gitignore" &&
      !entry.isSymbolicLink() &&
      entry.isFile(),
  );
  if (!hasGitignore) {
    return matchers;
  }
  const ignorePath = path.join(directory, ".gitignore");
  if (isIgnored(ignorePath, matchers)) {
    return matchers;
  }
  const matcher = await readIgnoreMatcher(ignorePath);
  return matcher === undefined ? matchers : [...matchers, matcher];
}

async function readIgnoreMatcher(ignorePath: string): Promise<ScopedIgnore | undefined> {
  try {
    const content = await fs.readFile(ignorePath, "utf8");
    return { basePath: path.dirname(ignorePath), matcher: ignore().add(content) };
  } catch (error) {
    if (!isNodeError(error, "ENOENT")) {
      // An unreadable ignore file is non-fatal, matching Git discovery resilience.
    }
    return undefined;
  }
}

function isIgnored(
  filePath: string,
  matchers: readonly ScopedIgnore[],
  directory = false,
): boolean {
  let ignored = false;
  for (const scoped of matchers) {
    if (!isPathInside(scoped.basePath, filePath)) {
      continue;
    }
    const relative = normalizeRelativePath(path.relative(scoped.basePath, filePath));
    if (relative === "" || relative.startsWith("..")) {
      continue;
    }
    const candidate = directory ? `${trimTrailingSlashes(relative)}/` : relative;
    const result = scoped.matcher.test(candidate);
    if (result.ignored) {
      ignored = true;
    }
    if (result.unignored) {
      ignored = false;
    }
  }
  return ignored;
}

function isExcludedFile(filePath: string): boolean {
  const basename = path.basename(filePath).toLowerCase();
  return EXCLUDED_FILENAMES.has(basename) || EXCLUDED_EXTENSIONS.has(path.extname(basename));
}

function isExcludedPath(
  workspaceRoot: string,
  filePath: string,
  excludes: readonly string[],
): boolean {
  const relative = path.relative(workspaceRoot, filePath);
  if (relative === "" || relative.startsWith("..") || path.isAbsolute(relative)) {
    return false;
  }
  const normalized = normalizeCodeCountExcludePath(relative).toLowerCase();
  const parts = normalized.split("/");
  return excludes.some((exclude) => {
    const value = normalizeCodeCountExcludePath(exclude).toLowerCase();
    return value.includes("/")
      ? normalized === value || normalized.startsWith(`${value}/`)
      : parts.includes(value);
  });
}

function trimTrailingSlashes(value: string): string {
  return value.replace(/\/+$/, "");
}

function throwIfCancelled(token: vscode.CancellationToken | undefined): void {
  if (token?.isCancellationRequested) {
    throw new vscode.CancellationError();
  }
}

function isNodeError(error: unknown, code: string): boolean {
  return error instanceof Error && "code" in error && (error as NodeJS.ErrnoException).code === code;
}
