import * as fs from "fs/promises";
import * as path from "path";
import { spawn } from "child_process";
import { applyEdits, modify, parse, ParseError, printParseErrorCode } from "jsonc-parser";
import { TerminalLogLevel } from "./terminalLogger";

export type DependencyMode = "pinned" | "latest" | "manual";

export interface DependencyEntry {
  readonly remote?: unknown;
  commit?: unknown;
  readonly abiGroup?: unknown;
}

export interface LockData {
  depsMode?: unknown;
  depsManualPath?: unknown;
  dependencies?: unknown;
}

export interface LockStatus {
  readonly mode: DependencyMode | undefined;
}

export interface DependencyComparison {
  readonly sampleMode: DependencyMode | undefined;
  readonly activeMode: DependencyMode | undefined;
  readonly rows: readonly DependencyComparisonRow[];
}

export interface DependencyComparisonRow {
  readonly name: string;
  readonly samplePresent: boolean;
  readonly sampleCommit: string | undefined;
  readonly activePresent: boolean;
  readonly activeCommit: string | undefined;
  readonly activeMode: DependencyMode | undefined;
}

export interface PinLatestResult {
  readonly updatedDependencies: readonly string[];
}

export interface UpdateUsedResult {
  readonly updatedDependencies: readonly string[];
}

export type UpdateRunner = (repoRoot: string) => Promise<void>;

export interface LockWorkflowOutput {
  log(level: TerminalLogLevel, value: string): void;
}

export interface ManualPathDirtyCheckResult {
  readonly dirty: boolean;
  readonly statusLines: readonly string[];
}

export type ManualPathDirtyChecker = (
  manualPath: string,
) => Promise<ManualPathDirtyCheckResult>;

export interface LockWorkflowOptions {
  readonly output?: LockWorkflowOutput;
  readonly dirtyChecker?: ManualPathDirtyChecker;
}

const ACTIVE_LOCK_NAME = "source_roots.lock.jsonc";
const TEMPLATE_LOCK_NAME = "source_roots.lock.jsonc.in";

export async function readActiveLockStatus(repoRoot: string): Promise<LockStatus> {
  const data = await loadLockData(activeLockPath(repoRoot));
  return { mode: dependencyMode(data.depsMode) };
}

export async function readDependencyComparison(
  repoRoot: string,
): Promise<DependencyComparison> {
  const samplePath = templateLockPath(repoRoot);
  const activePath = activeLockPath(repoRoot);
  const [sample, active] = await Promise.all([
    loadLockData(samplePath),
    loadLockData(activePath),
  ]);
  const sampleDependencies = dependencyEntries(sample.dependencies, samplePath);
  const activeDependencies = dependencyEntries(active.dependencies, activePath);
  const activeMode = dependencyMode(active.depsMode);
  const activeNames = new Set(Object.keys(activeDependencies));
  const sampleNames = Object.keys(sampleDependencies);
  const activeOnlyNames = Object.keys(activeDependencies).filter(
    (name) => !Object.prototype.hasOwnProperty.call(sampleDependencies, name),
  );

  return {
    sampleMode: dependencyMode(sample.depsMode),
    activeMode,
    rows: [...sampleNames, ...activeOnlyNames].map((name) => {
      const activePresent = activeNames.has(name);
      return {
        name,
        samplePresent: Object.prototype.hasOwnProperty.call(sampleDependencies, name),
        sampleCommit: dependencyCommit(sampleDependencies[name]),
        activePresent,
        activeCommit: dependencyCommit(activeDependencies[name]),
        activeMode: activePresent ? effectiveDependencyMode(activeMode, active, name) : undefined,
      };
    }),
  };
}

export async function usePinned(
  repoRoot: string,
  options: LockWorkflowOptions = {},
): Promise<void> {
  const templatePath = templateLockPath(repoRoot);
  const activePath = activeLockPath(repoRoot);
  const template = await loadLockData(templatePath);
  const activeText = await readLockText(activePath);
  const active = parseLockText(activeText, activePath);
  if (dependencyMode(active.depsMode) === "manual") {
    await assertCurrentManualPathsClean(repoRoot, active, "Use pinned", options);
  }
  const dependencies = dependencyEntries(template.dependencies, templatePath);

  let nextText = setJsonValue(activeText, ["depsMode"], "pinned");
  nextText = setJsonValue(nextText, ["dependencies"], dependencies);
  nextText = setJsonValue(nextText, ["depsManualPath"], emptyManualPathMap(dependencies));
  parseLockText(nextText, activePath);

  await fs.writeFile(activePath, ensureTrailingNewline(nextText), "utf8");
}

export async function manualAll(
  repoRoot: string,
  options: LockWorkflowOptions = {},
): Promise<void> {
  const activePath = activeLockPath(repoRoot);
  const activeText = await readLockText(activePath);
  const active = parseLockText(activeText, activePath);
  if (dependencyMode(active.depsMode) === "manual") {
    await assertCurrentManualPathsClean(repoRoot, active, "Manual all", options);
  }
  const dependencies = dependencyEntries(active.dependencies, activePath);

  let nextText = setJsonValue(activeText, ["depsMode"], "manual");
  nextText = setJsonValue(nextText, ["depsManualPath"], manualPathMap(dependencies));
  parseLockText(nextText, activePath);

  await fs.writeFile(activePath, ensureTrailingNewline(nextText), "utf8");
}

export async function pinLatest(
  repoRoot: string,
  runUpdate: UpdateRunner,
  options: LockWorkflowOptions = {},
): Promise<PinLatestResult> {
  const activePath = activeLockPath(repoRoot);
  const activeText = await readLockText(activePath);
  const active = parseLockText(activeText, activePath);
  dependencyEntries(active.dependencies, activePath);
  if (dependencyMode(active.depsMode) === "manual") {
    await assertCurrentManualPathsClean(repoRoot, active, "Pin latest", options);
  }

  const latestActiveText = setJsonValue(activeText, ["depsMode"], "latest");
  parseLockText(latestActiveText, activePath);
  await fs.writeFile(activePath, ensureTrailingNewline(latestActiveText), "utf8");

  try {
    await runUpdate(repoRoot);
  } catch (error) {
    await fs.writeFile(activePath, ensureTrailingNewline(activeText), "utf8");
    throw error;
  }

  const updatedActive = await loadLockData(activePath);
  const activeDependencies = dependencyEntries(updatedActive.dependencies, activePath);
  let pinnedActiveText = await readLockText(activePath);
  pinnedActiveText = setJsonValue(pinnedActiveText, ["depsMode"], "pinned");
  pinnedActiveText = setJsonValue(
    pinnedActiveText,
    ["depsManualPath"],
    emptyManualPathMap(activeDependencies),
  );
  parseLockText(pinnedActiveText, activePath);
  await fs.writeFile(activePath, ensureTrailingNewline(pinnedActiveText), "utf8");

  return {
    updatedDependencies: Object.keys(activeDependencies),
  };
}

export async function updateUsed(repoRoot: string): Promise<UpdateUsedResult> {
  const activePath = activeLockPath(repoRoot);
  const templatePath = templateLockPath(repoRoot);
  const active = await loadLockData(activePath);
  const mode = dependencyMode(active.depsMode);
  if (mode !== "pinned" && mode !== "latest") {
    throw new Error("Update used requires active lock depsMode to be pinned or latest.");
  }
  const activeDependencies = dependencyEntries(active.dependencies, activePath);
  const templateText = await readLockText(templatePath);
  const template = parseLockText(templateText, templatePath);
  const templateDependencies = dependencyEntries(template.dependencies, templatePath);
  const updatedTemplateDependencies = copyTemplateDependenciesWithCommits(
    templateDependencies,
    activeDependencies,
    templatePath,
  );

  let nextTemplateText = setJsonValue(templateText, ["depsMode"], "pinned");
  nextTemplateText = setJsonValue(
    nextTemplateText,
    ["dependencies"],
    updatedTemplateDependencies,
  );
  nextTemplateText = setJsonValue(
    nextTemplateText,
    ["depsManualPath"],
    emptyManualPathMap(updatedTemplateDependencies),
  );
  parseLockText(nextTemplateText, templatePath);

  await fs.writeFile(templatePath, ensureTrailingNewline(nextTemplateText), "utf8");

  return {
    updatedDependencies: Object.keys(updatedTemplateDependencies),
  };
}

function activeLockPath(repoRoot: string): string {
  return path.join(repoRoot, ACTIVE_LOCK_NAME);
}

function templateLockPath(repoRoot: string): string {
  return path.join(repoRoot, TEMPLATE_LOCK_NAME);
}

async function loadLockData(filePath: string): Promise<LockData> {
  return parseLockText(await readLockText(filePath), filePath);
}

async function readLockText(filePath: string): Promise<string> {
  try {
    return await fs.readFile(filePath, "utf8");
  } catch (error) {
    throw new Error(`Unable to read ${filePath}: ${errorMessage(error)}`);
  }
}

function parseLockText(text: string, filePath: string): LockData {
  const errors: ParseError[] = [];
  const value = parse(text, errors, { allowTrailingComma: true });
  if (errors.length > 0) {
    const details = errors
      .map((error) => `${printParseErrorCode(error.error)} at offset ${error.offset}`)
      .join(", ");
    throw new Error(`Invalid JSONC in ${filePath}: ${details}`);
  }
  if (!isObject(value)) {
    throw new Error(`Invalid lock file ${filePath}: expected top-level object`);
  }
  return value as LockData;
}

function dependencyMode(value: unknown): DependencyMode | undefined {
  return value === "pinned" || value === "latest" || value === "manual"
    ? value
    : undefined;
}

function dependencyEntries(value: unknown, filePath: string): Record<string, DependencyEntry> {
  if (!isObject(value)) {
    throw new Error(`Invalid dependencies map in ${filePath}`);
  }

  const dependencies: Record<string, DependencyEntry> = {};
  for (const [name, entry] of Object.entries(value)) {
    if (!isSafeDependencyName(name)) {
      throw new Error(`Invalid dependency name ${JSON.stringify(name)} in ${filePath}`);
    }
    if (!isObject(entry)) {
      throw new Error(`Invalid dependency entry for ${name} in ${filePath}`);
    }
    dependencies[name] = { ...(entry as Record<string, unknown>) };
  }
  return dependencies;
}

function dependencyCommit(entry: DependencyEntry | undefined): string | undefined {
  return typeof entry?.commit === "string" ? entry.commit : undefined;
}

function effectiveDependencyMode(
  mode: DependencyMode | undefined,
  lockData: LockData,
  dependencyName: string,
): DependencyMode | undefined {
  if (mode !== "manual") {
    return mode;
  }
  return hasManualPathOverride(lockData.depsManualPath, dependencyName) ? "manual" : "pinned";
}

function hasManualPathOverride(value: unknown, dependencyName: string): boolean {
  if (!isObject(value)) {
    return false;
  }
  const manualPath = value[dependencyName];
  return typeof manualPath === "string" && manualPath.trim() !== "";
}

function emptyManualPathMap(
  dependencies: Record<string, DependencyEntry>,
): Record<string, string> {
  return Object.fromEntries(Object.keys(dependencies).map((name) => [name, ""]));
}

function manualPathMap(
  dependencies: Record<string, DependencyEntry>,
): Record<string, string> {
  return Object.fromEntries(
    Object.keys(dependencies).map((name) => [
      name,
      path.posix.join("build", "dependency_seed_repos", name),
    ]),
  );
}

async function assertCurrentManualPathsClean(
  repoRoot: string,
  active: LockData,
  operation: string,
  options: LockWorkflowOptions,
): Promise<void> {
  const entries = manualPathEntries(active.depsManualPath, repoRoot);
  if (entries.length === 0) {
    return;
  }

  const dirtyChecker = options.dirtyChecker ?? gitManualPathDirtyChecker;
  const dirtyEntries: Array<{
    readonly dependency: string;
    readonly manualPath: string;
    readonly statusLines: readonly string[];
  }> = [];

  for (const entry of entries) {
    const result = await dirtyChecker(entry.absolutePath);
    if (result.dirty) {
      dirtyEntries.push({
        dependency: entry.dependency,
        manualPath: entry.absolutePath,
        statusLines: result.statusLines,
      });
    }
  }

  if (dirtyEntries.length === 0) {
    return;
  }

  const output = options.output;
  output?.log(
    "error",
    `Refusing ${operation}: manual dependency worktree(s) are dirty.`,
  );
  for (const entry of dirtyEntries) {
    output?.log("context", `${entry.dependency}: ${entry.manualPath}`);
    for (const line of entry.statusLines) {
      output?.log("warning", `  ${line}`);
    }
  }

  throw new Error(
    `${operation} stopped because ${dirtyEntries.length} manual dependency worktree(s) are dirty. See the FreeCM output for details.`,
  );
}

function manualPathEntries(
  value: unknown,
  repoRoot: string,
): Array<{ readonly dependency: string; readonly absolutePath: string }> {
  if (!isObject(value)) {
    return [];
  }

  const entries: Array<{ readonly dependency: string; readonly absolutePath: string }> = [];
  const seenPaths = new Set<string>();
  for (const [dependency, configuredPath] of Object.entries(value)) {
    if (typeof configuredPath !== "string" || configuredPath.trim() === "") {
      continue;
    }
    const absolutePath = path.resolve(repoRoot, configuredPath);
    if (seenPaths.has(absolutePath)) {
      continue;
    }
    seenPaths.add(absolutePath);
    entries.push({ dependency, absolutePath });
  }
  return entries;
}

async function gitManualPathDirtyChecker(
  manualPath: string,
): Promise<ManualPathDirtyCheckResult> {
  let stat: Awaited<ReturnType<typeof fs.stat>>;
  try {
    stat = await fs.stat(manualPath);
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) {
      return { dirty: false, statusLines: [] };
    }
    throw new Error(`Unable to inspect manual dependency path ${manualPath}: ${errorMessage(error)}`);
  }
  if (!stat.isDirectory()) {
    return { dirty: false, statusLines: [] };
  }

  const result = await runGitStatus(manualPath);
  if (result.exitCode !== 0) {
    const details = [...result.stderrLines, ...result.stdoutLines].join("\n").trim();
    if (/not a git repository/i.test(details)) {
      return { dirty: false, statusLines: [] };
    }
    throw new Error(
      `Unable to inspect manual dependency git status at ${manualPath}: ${
        details || `git exited with code ${result.exitCode}`
      }`,
    );
  }
  return {
    dirty: result.stdoutLines.length > 0,
    statusLines: result.stdoutLines,
  };
}

async function runGitStatus(
  cwd: string,
): Promise<{ readonly exitCode: number | null; readonly stdoutLines: string[]; readonly stderrLines: string[] }> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      "git",
      ["status", "--porcelain=v1", "--untracked-files=all"],
      {
        cwd,
        shell: false,
      },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer | string) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer | string) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (exitCode) => {
      resolve({
        exitCode,
        stdoutLines: splitNonEmptyLines(stdout),
        stderrLines: splitNonEmptyLines(stderr),
      });
    });
  });
}

function copyTemplateDependenciesWithCommits(
  templateDependencies: Record<string, DependencyEntry>,
  activeDependencies: Record<string, DependencyEntry>,
  filePath: string,
): Record<string, DependencyEntry> {
  const next: Record<string, DependencyEntry> = {};
  for (const [name, templateEntry] of Object.entries(templateDependencies)) {
    const activeEntry = activeDependencies[name];
    if (activeEntry === undefined) {
      throw new Error(`Dependency ${name} is missing from active lock while updating ${filePath}`);
    }
    if (typeof activeEntry.commit !== "string" || activeEntry.commit.trim() === "") {
      throw new Error(`Dependency ${name} has no resolved commit in active lock`);
    }
    next[name] = {
      ...templateEntry,
      commit: activeEntry.commit.trim(),
    };
  }
  return next;
}

function setJsonValue(text: string, propertyPath: (string | number)[], value: unknown): string {
  const edits = modify(text, propertyPath, value, {
    formattingOptions: {
      insertSpaces: true,
      tabSize: 2,
      eol: "\n",
    },
  });
  return applyEdits(text, edits);
}

function ensureTrailingNewline(text: string): string {
  return text.endsWith("\n") ? text : `${text}\n`;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSafeDependencyName(name: string): boolean {
  return /^[A-Za-z0-9_.-]+$/.test(name);
}

function splitNonEmptyLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0);
}

function isNodeErrorCode(error: unknown, code: string): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { code?: unknown }).code === code
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
