import * as fs from "fs/promises";
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
  const ignoreMatchers = await loadIgnoreMatchers(
    input.workspaceRoot,
    input.targetPath,
    input.maxFiles,
    input.cancellationToken,
  );
  const finish = beginFilesystemRead();
  let uris: vscode.Uri[];
  try {
    uris = await vscode.workspace.findFiles(
      new vscode.RelativePattern(vscode.Uri.file(input.targetPath), input.table.candidateGlob()),
      "{**/.git/**,**/.freecm/counts/**}",
      undefined,
      input.cancellationToken,
    );
  } finally {
    finish();
  }
  throwIfCancelled(input.cancellationToken);
  const candidates: SourceCandidate[] = [];
  for (const uri of uris) {
    throwIfCancelled(input.cancellationToken);
    if (uri.scheme !== "file") continue;
    const counter = input.table.getCounter(uri.fsPath);
    if (
      counter === undefined ||
      isExcludedFile(uri.fsPath) ||
      EXCLUDED_LANGUAGES.has(counter.name.toLowerCase()) ||
      !isPathInside(input.targetPath, uri.fsPath) ||
      isPathInside(input.outputRoot, uri.fsPath) ||
      isExcludedPath(input.workspaceRoot, uri.fsPath, [...INTERNAL_EXCLUDES, ...input.excludePaths]) ||
      isIgnored(uri.fsPath, ignoreMatchers)
    ) {
      continue;
    }
    candidates.push({ uri, counter });
    if (candidates.length > input.maxFiles) {
      throw new Error(
        `Code count found more than maxFiles=${input.maxFiles} supported source files. Narrow the target or increase freecm.codeCount.maxFiles.`,
      );
    }
  }
  return candidates;
}

async function loadIgnoreMatchers(
  workspaceRoot: string,
  targetPath: string,
  maxFiles: number,
  token: vscode.CancellationToken | undefined,
): Promise<ScopedIgnore[]> {
  const paths = new Set<string>();
  let directory = path.resolve(targetPath);
  const workspace = path.resolve(workspaceRoot);
  while (isPathInside(workspace, directory)) {
    paths.add(path.join(directory, ".gitignore"));
    if (directory === workspace) break;
    directory = path.dirname(directory);
  }
  const finishDiscovery = beginFilesystemRead();
  try {
    const nested = await vscode.workspace.findFiles(
      new vscode.RelativePattern(vscode.Uri.file(targetPath), "**/.gitignore"),
      "{**/.git/**,**/.freecm/counts/**}",
      undefined,
      token,
    );
    for (const uri of nested) {
      if (
        uri.scheme === "file" &&
        path.basename(uri.fsPath) === ".gitignore" &&
        isPathInside(targetPath, uri.fsPath)
      ) {
        paths.add(uri.fsPath);
      }
    }
  } finally {
    finishDiscovery();
  }
  const matchers: ScopedIgnore[] = [];
  for (const ignorePath of [...paths].sort((a, b) => a.length - b.length)) {
    throwIfCancelled(token);
    if (isIgnored(ignorePath, matchers)) continue;
    const finishRead = beginFilesystemRead();
    try {
      const content = await fs.readFile(ignorePath, "utf8");
      matchers.push({ basePath: path.dirname(ignorePath), matcher: ignore().add(content) });
    } catch (error) {
      if (!isNodeError(error, "ENOENT")) {
        // An unreadable ignore file is non-fatal, matching Git discovery resilience.
      }
    } finally {
      finishRead();
    }
  }
  return matchers;
}

function isIgnored(filePath: string, matchers: readonly ScopedIgnore[]): boolean {
  let ignored = false;
  for (const scoped of matchers) {
    if (!isPathInside(scoped.basePath, filePath)) continue;
    const relative = normalizeRelativePath(path.relative(scoped.basePath, filePath));
    if (relative === "" || relative.startsWith("..")) continue;
    const result = scoped.matcher.test(relative);
    if (result.ignored) ignored = true;
    if (result.unignored) ignored = false;
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
  if (relative === "" || relative.startsWith("..") || path.isAbsolute(relative)) return false;
  const normalized = normalizeCodeCountExcludePath(relative).toLowerCase();
  const parts = normalized.split("/");
  return excludes.some((exclude) => {
    const value = normalizeCodeCountExcludePath(exclude).toLowerCase();
    return value.includes("/")
      ? normalized === value || normalized.startsWith(`${value}/`)
      : parts.includes(value);
  });
}

function throwIfCancelled(token: vscode.CancellationToken | undefined): void {
  if (token?.isCancellationRequested) throw new vscode.CancellationError();
}

function isNodeError(error: unknown, code: string): boolean {
  return error instanceof Error && "code" in error && (error as NodeJS.ErrnoException).code === code;
}
