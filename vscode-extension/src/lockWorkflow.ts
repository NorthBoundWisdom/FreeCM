import * as fs from "fs/promises";
import * as path from "path";
import { spawn } from "child_process";
import { applyEdits, modify } from "jsonc-parser";
import { atomicWriteText } from "./atomicWrite";
import {
  ACTIVE_LOCK_NAME,
  DependencyMode,
  TEMPLATE_LOCK_NAME,
  dependencyMode,
  isSafeDependencyName,
} from "./lockSchema";
import {
  DependencyEntry,
  LockData,
  dependencyEntries,
  parseLockText,
} from "./lockSchemaValidation";
import { TerminalLogLevel } from "./terminalLogger";
import { withWorkspaceLock } from "./workspaceLock";
import {
  beginFilesystemRead,
  beginGitProcess,
} from "./performanceMetrics";

export type { DependencyEntry, LockData } from "./lockSchemaValidation";

export interface LockStatus {
  readonly mode: DependencyMode | undefined;
}

export interface LockRefreshSnapshot {
  readonly samplePath: string;
  readonly activePath: string;
  readonly sample: LockData;
  readonly active: LockData;
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
  readonly activeManualPath?: string | undefined;
  readonly activeManualPathStatus?: ManualPathStatus | undefined;
}

export type ManualPathStatus = "clean" | "dirty" | "untracked";

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

const MANUAL_GIT_CONCURRENCY = 4;
export const MANUAL_STATUS_TTL_MS = 500;
export const GIT_OUTPUT_LIMIT_BYTES = 64 * 1024;
const GIT_STATUS_ARGS = [
  "status",
  "--porcelain=v1",
  "--untracked-files=all",
] as const;

interface ManualStatusCacheEntry {
  readonly expiresAt: number;
  readonly value: Promise<ManualPathStatus>;
}

const manualStatusCache = new Map<string, ManualStatusCacheEntry>();

export async function readLockRefreshSnapshot(
  repoRoot: string,
): Promise<LockRefreshSnapshot> {
  const samplePath = templateLockPath(repoRoot);
  const activePath = await readableActiveLockPath(repoRoot);
  if (activePath === samplePath) {
    const sample = await loadLockData(samplePath);
    return { samplePath, activePath, sample, active: sample };
  }
  const [sample, active] = await Promise.all([
    loadLockData(samplePath),
    loadLockData(activePath),
  ]);
  return { samplePath, activePath, sample, active };
}

export async function readActiveLockStatus(
  repoRoot: string,
  snapshot?: LockRefreshSnapshot,
): Promise<LockStatus> {
  const data = snapshot?.active ?? (await loadLockData(await readableActiveLockPath(repoRoot)));
  return { mode: dependencyMode(data.depsMode) };
}

export async function readDependencyComparison(
  repoRoot: string,
  snapshot?: LockRefreshSnapshot,
): Promise<DependencyComparison> {
  const current = snapshot ?? (await readLockRefreshSnapshot(repoRoot));
  const { samplePath, activePath, sample, active } = current;
  const sampleDependencies = dependencyEntries(sample.dependencies, samplePath);
  const activeDependencies = dependencyEntries(active.dependencies, activePath);
  const activeMode = dependencyMode(active.depsMode);
  const activeNames = new Set(Object.keys(activeDependencies));
  const sampleNames = Object.keys(sampleDependencies);
  const activeOnlyNames = Object.keys(activeDependencies).filter(
    (name) => !Object.prototype.hasOwnProperty.call(sampleDependencies, name),
  );

  const rows = await mapWithConcurrency(
    [...sampleNames, ...activeOnlyNames],
    MANUAL_GIT_CONCURRENCY,
    async (name) => {
      const activePresent = activeNames.has(name);
      const rowActiveMode = activePresent
        ? effectiveDependencyMode(activeMode, active, name)
        : undefined;
      const manualPathStatus =
        rowActiveMode === "manual"
          ? await readManualDependencyPathStatus(
              repoRoot,
              active.depsManualPath,
              name,
            )
          : undefined;
      return {
        name,
        samplePresent: Object.prototype.hasOwnProperty.call(
          sampleDependencies,
          name,
        ),
        sampleCommit: dependencyCommit(sampleDependencies[name]),
        activePresent,
        activeCommit: dependencyCommit(activeDependencies[name]),
        activeMode: rowActiveMode,
        ...(manualPathStatus === undefined
          ? {}
          : {
              activeManualPath: manualPathStatus.manualPath,
              activeManualPathStatus: manualPathStatus.status,
            }),
      };
    },
  );

  return {
    sampleMode: dependencyMode(sample.depsMode),
    activeMode,
    rows,
  };
}

export async function usePinned(
  repoRoot: string,
  options: LockWorkflowOptions = {},
): Promise<void> {
  await withWorkspaceLock(repoRoot, () => usePinnedUnlocked(repoRoot, options));
}

async function usePinnedUnlocked(
  repoRoot: string,
  options: LockWorkflowOptions,
): Promise<void> {
  const templatePath = templateLockPath(repoRoot);
  const activePath = await ensureActiveLockPath(repoRoot);
  const template = await loadLockData(templatePath);
  const activeText = await readLockText(activePath);
  const active = parseLockText(activeText, activePath);
  if (dependencyMode(active.depsMode) === "manual") {
    await assertCurrentManualPathsClean(
      repoRoot,
      active,
      "Use pinned",
      options,
    );
  }
  const dependencies = dependencyEntries(template.dependencies, templatePath);

  let nextText = setJsonValue(activeText, ["depsMode"], "pinned");
  nextText = setJsonValue(nextText, ["dependencies"], dependencies);
  nextText = setJsonValue(
    nextText,
    ["depsManualPath"],
    emptyManualPathMap(dependencies),
  );
  parseLockText(nextText, activePath);

  await writeLockText(activePath, nextText);
}

export async function manualAll(
  repoRoot: string,
  options: LockWorkflowOptions = {},
): Promise<void> {
  await withWorkspaceLock(repoRoot, () => manualAllUnlocked(repoRoot, options));
}

async function manualAllUnlocked(
  repoRoot: string,
  options: LockWorkflowOptions,
): Promise<void> {
  const activePath = await ensureActiveLockPath(repoRoot);
  const activeText = await readLockText(activePath);
  const active = parseLockText(activeText, activePath);
  if (dependencyMode(active.depsMode) === "manual") {
    await assertCurrentManualPathsClean(
      repoRoot,
      active,
      "Manual all",
      options,
    );
  }
  const dependencies = dependencyEntries(active.dependencies, activePath);

  let nextText = setJsonValue(activeText, ["depsMode"], "manual");
  nextText = setJsonValue(nextText, ["dependencies"], dependencies);
  nextText = setJsonValue(
    nextText,
    ["depsManualPath"],
    manualPathMap(dependencies),
  );
  parseLockText(nextText, activePath);

  await writeLockText(activePath, nextText);
}

export async function manualDependency(
  repoRoot: string,
  dependencyName: string,
): Promise<void> {
  await withWorkspaceLock(repoRoot, () =>
    manualDependencyUnlocked(repoRoot, dependencyName),
  );
}

async function manualDependencyUnlocked(
  repoRoot: string,
  dependencyName: string,
): Promise<void> {
  assertSafeDependencyName(dependencyName);
  const activePath = await ensureActiveLockPath(repoRoot);
  const activeText = await readLockText(activePath);
  const active = parseLockText(activeText, activePath);
  const dependencies = dependencyEntries(active.dependencies, activePath);
  if (dependencies[dependencyName] === undefined) {
    throw new Error(`Dependency ${dependencyName} is missing from active lock`);
  }
  const seedRepoName =
    dependencies[dependencyName].repoName ?? dependencyName;
  const nextManualPath = currentManualPathMap(
    active.depsManualPath,
    dependencies,
    activePath,
  );
  nextManualPath[dependencyName] = dependencySeedPath(seedRepoName);

  let nextText = setJsonValue(activeText, ["depsMode"], "manual");
  nextText = setJsonValue(nextText, ["depsManualPath"], nextManualPath);
  parseLockText(nextText, activePath);

  await writeLockText(activePath, nextText);
}

export async function restoreDependencyPin(
  repoRoot: string,
  dependencyName: string,
): Promise<void> {
  await withWorkspaceLock(repoRoot, () =>
    restoreDependencyPinUnlocked(repoRoot, dependencyName),
  );
}

async function restoreDependencyPinUnlocked(
  repoRoot: string,
  dependencyName: string,
): Promise<void> {
  assertSafeDependencyName(dependencyName);
  const activePath = await ensureActiveLockPath(repoRoot);
  const activeText = await readLockText(activePath);
  const active = parseLockText(activeText, activePath);
  const dependencies = dependencyEntries(active.dependencies, activePath);
  if (dependencies[dependencyName] === undefined) {
    throw new Error(`Dependency ${dependencyName} is missing from active lock`);
  }
  const nextManualPath = currentManualPathMap(
    active.depsManualPath,
    dependencies,
    activePath,
  );
  nextManualPath[dependencyName] = "";

  const nextText = setJsonValue(activeText, ["depsManualPath"], nextManualPath);
  parseLockText(nextText, activePath);

  await writeLockText(activePath, nextText);
}

export async function applyActiveDependencyToSample(
  repoRoot: string,
  dependencyName: string,
): Promise<void> {
  await withWorkspaceLock(repoRoot, async () => {
    assertSafeDependencyName(dependencyName);
    const templatePath = templateLockPath(repoRoot);
    const activePath = activeLockPath(repoRoot);
    const templateText = await readLockText(templatePath);
    const template = parseLockText(templateText, templatePath);
    const active = await loadLockData(activePath);
    const templateDependencies = dependencyEntries(
      template.dependencies,
      templatePath,
    );
    const activeDependencies = dependencyEntries(
      active.dependencies,
      activePath,
    );
    if (templateDependencies[dependencyName] === undefined) {
      throw new Error(`Dependency ${dependencyName} is missing from sample lock`);
    }
    if (activeDependencies[dependencyName] === undefined) {
      throw new Error(`Dependency ${dependencyName} is missing from active lock`);
    }

    const commit = await activeDependencyCommitForSample(
      repoRoot,
      active,
      activeDependencies,
      dependencyName,
    );
    const nextTemplateText = setJsonValue(
      templateText,
      ["dependencies", dependencyName, "commit"],
      commit,
    );
    parseLockText(nextTemplateText, templatePath);
    await writeLockText(templatePath, nextTemplateText);
  });
}

async function activeDependencyCommitForSample(
  repoRoot: string,
  active: LockData,
  activeDependencies: Record<string, DependencyEntry>,
  dependencyName: string,
): Promise<string> {
  const mode = effectiveDependencyMode(
    dependencyMode(active.depsMode),
    active,
    dependencyName,
  );
  if (mode === "manual") {
    const configuredPath = manualPathOverride(
      active.depsManualPath,
      dependencyName,
    );
    if (configuredPath === undefined) {
      throw new Error(`Dependency ${dependencyName} has no manual path`);
    }
    return readGitHeadCommit(
      resolveManualPath(repoRoot, configuredPath),
      dependencyName,
    );
  }

  const commit = dependencyCommit(activeDependencies[dependencyName]);
  if (commit === undefined || commit.trim() === "") {
    throw new Error(`Dependency ${dependencyName} has no active commit`);
  }
  return commit.trim();
}

export async function pinLatest(
  repoRoot: string,
  runUpdate: UpdateRunner,
  options: LockWorkflowOptions = {},
): Promise<PinLatestResult> {
  const originalText = await withWorkspaceLock(repoRoot, () =>
    beginPinLatest(repoRoot, options),
  );
  try {
    await runUpdate(repoRoot);
  } catch (error) {
    await withWorkspaceLock(repoRoot, () => restorePinLatest(repoRoot, originalText));
    throw error;
  }
  return withWorkspaceLock(repoRoot, () => finishPinLatest(repoRoot));
}

async function beginPinLatest(
  repoRoot: string,
  options: LockWorkflowOptions,
): Promise<string> {
  const activePath = await ensureActiveLockPath(repoRoot);
  const activeText = await readLockText(activePath);
  const active = parseLockText(activeText, activePath);
  const dependencies = dependencyEntries(active.dependencies, activePath);
  if (dependencyMode(active.depsMode) === "manual") {
    await assertCurrentManualPathsClean(
      repoRoot,
      active,
      "Pin latest",
      options,
    );
  }

  let latestActiveText = setJsonValue(activeText, ["depsMode"], "latest");
  latestActiveText = setJsonValue(
    latestActiveText,
    ["dependencies"],
    dependencies,
  );
  parseLockText(latestActiveText, activePath);
  await writeLockText(activePath, latestActiveText);
  return activeText;
}

async function restorePinLatest(repoRoot: string, activeText: string): Promise<void> {
  await writeLockText(activeLockPath(repoRoot), activeText);
}

async function finishPinLatest(repoRoot: string): Promise<PinLatestResult> {
  const activePath = activeLockPath(repoRoot);
  const updatedActive = await loadLockData(activePath);
  const activeDependencies = dependencyEntries(
    updatedActive.dependencies,
    activePath,
  );
  let pinnedActiveText = await readLockText(activePath);
  pinnedActiveText = setJsonValue(pinnedActiveText, ["depsMode"], "pinned");
  pinnedActiveText = setJsonValue(
    pinnedActiveText,
    ["dependencies"],
    activeDependencies,
  );
  pinnedActiveText = setJsonValue(
    pinnedActiveText,
    ["depsManualPath"],
    emptyManualPathMap(activeDependencies),
  );
  parseLockText(pinnedActiveText, activePath);
  await writeLockText(activePath, pinnedActiveText);

  return {
    updatedDependencies: Object.keys(activeDependencies),
  };
}

export async function updateUsed(repoRoot: string): Promise<UpdateUsedResult> {
  return withWorkspaceLock(repoRoot, () => updateUsedUnlocked(repoRoot));
}

async function updateUsedUnlocked(repoRoot: string): Promise<UpdateUsedResult> {
  const activePath = await ensureActiveLockPath(repoRoot);
  const templatePath = templateLockPath(repoRoot);
  const active = await loadLockData(activePath);
  const mode = dependencyMode(active.depsMode);
  if (mode !== "pinned" && mode !== "latest") {
    throw new Error(
      "Update used requires active lock depsMode to be pinned or latest.",
    );
  }
  const activeDependencies = dependencyEntries(active.dependencies, activePath);
  const templateText = await readLockText(templatePath);
  const template = parseLockText(templateText, templatePath);
  const templateDependencies = dependencyEntries(
    template.dependencies,
    templatePath,
  );
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

  await writeLockText(templatePath, nextTemplateText);

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

async function readableActiveLockPath(repoRoot: string): Promise<string> {
  const activePath = activeLockPath(repoRoot);
  let finishRead = beginFilesystemRead();
  try {
    await fs.access(activePath);
    return activePath;
  } catch (error) {
    if (!isNodeErrorCode(error, "ENOENT")) {
      throw new Error(
        `Unable to inspect ${activePath}: ${errorMessage(error)}`,
      );
    }
  } finally {
    finishRead();
  }
  const templatePath = templateLockPath(repoRoot);
  finishRead = beginFilesystemRead();
  try {
    await fs.access(templatePath);
    return templatePath;
  } catch (error) {
    throw new Error(
      `Unable to read ${activePath} or ${templatePath}: ${errorMessage(error)}`,
    );
  } finally {
    finishRead();
  }
}

async function ensureActiveLockPath(repoRoot: string): Promise<string> {
  const activePath = activeLockPath(repoRoot);
  try {
    await fs.access(activePath);
    return activePath;
  } catch (error) {
    if (!isNodeErrorCode(error, "ENOENT")) {
      throw new Error(
        `Unable to inspect ${activePath}: ${errorMessage(error)}`,
      );
    }
  }

  const templatePath = templateLockPath(repoRoot);
  let templateText: string;
  try {
    templateText = await fs.readFile(templatePath, "utf8");
  } catch (error) {
    throw new Error(
      `Unable to create ${activePath} from ${templatePath}: ${errorMessage(error)}`,
    );
  }
  parseLockText(templateText, templatePath);
  await writeLockText(activePath, templateText);
  return activePath;
}

async function loadLockData(filePath: string): Promise<LockData> {
  return parseLockText(await readLockText(filePath), filePath);
}

async function readLockText(filePath: string): Promise<string> {
  const finishRead = beginFilesystemRead();
  try {
    return await fs.readFile(filePath, "utf8");
  } catch (error) {
    throw new Error(`Unable to read ${filePath}: ${errorMessage(error)}`);
  } finally {
    finishRead();
  }
}

function dependencyCommit(
  entry: DependencyEntry | undefined,
): string | undefined {
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
  return hasManualPathOverride(lockData.depsManualPath, dependencyName)
    ? "manual"
    : "pinned";
}

function hasManualPathOverride(
  value: unknown,
  dependencyName: string,
): boolean {
  return manualPathOverride(value, dependencyName) !== undefined;
}

function manualPathOverride(
  value: unknown,
  dependencyName: string,
): string | undefined {
  if (!isObject(value)) {
    return undefined;
  }
  const manualPath = value[dependencyName];
  return typeof manualPath === "string" && manualPath.trim() !== ""
    ? manualPath
    : undefined;
}

function emptyManualPathMap(
  dependencies: Record<string, DependencyEntry>,
): Record<string, string> {
  return Object.fromEntries(
    Object.keys(dependencies).map((name) => [name, ""]),
  );
}

function manualPathMap(
  dependencies: Record<string, DependencyEntry>,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(dependencies).map(([name, entry]) => [
      name,
      dependencySeedPath(entry.repoName ?? name),
    ]),
  );
}

function currentManualPathMap(
  value: unknown,
  dependencies: Record<string, DependencyEntry>,
  filePath: string,
): Record<string, string> {
  if (!isObject(value)) {
    throw new Error(`Invalid depsManualPath map in ${filePath}`);
  }
  return Object.fromEntries(
    Object.keys(dependencies).map((name) => {
      const manualPath = value[name];
      if (typeof manualPath !== "string") {
        throw new Error(`Invalid depsManualPath.${name} in ${filePath}`);
      }
      return [name, manualPath];
    }),
  );
}

function dependencySeedPath(seedRepoName: string): string {
  return path.posix.join("build", "dependency_seed_repos", seedRepoName);
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

  const checks = await mapWithConcurrency(
    entries,
    MANUAL_GIT_CONCURRENCY,
    async (entry) => ({ entry, result: await dirtyChecker(entry.absolutePath) }),
  );
  for (const { entry, result } of checks) {
    if (!result.dirty) {
      continue;
    }
    dirtyEntries.push({
      dependency: entry.dependency,
      manualPath: entry.absolutePath,
      statusLines: result.statusLines,
    });
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

  const entries: Array<{
    readonly dependency: string;
    readonly absolutePath: string;
  }> = [];
  const seenPaths = new Set<string>();
  for (const [dependency, configuredPath] of Object.entries(value)) {
    if (typeof configuredPath !== "string" || configuredPath.trim() === "") {
      continue;
    }
    const absolutePath = resolveManualPath(repoRoot, configuredPath);
    if (seenPaths.has(absolutePath)) {
      continue;
    }
    seenPaths.add(absolutePath);
    entries.push({ dependency, absolutePath });
  }
  return entries;
}

interface ManualDependencyPathStatus {
  readonly manualPath: string;
  readonly status: ManualPathStatus;
}

async function readManualDependencyPathStatus(
  repoRoot: string,
  value: unknown,
  dependencyName: string,
): Promise<ManualDependencyPathStatus | undefined> {
  const configuredPath = manualPathOverride(value, dependencyName);
  if (configuredPath === undefined) {
    return undefined;
  }
  const manualPath = resolveManualPath(repoRoot, configuredPath);
  return {
    manualPath,
    status: await cachedManualPathStatus(manualPath),
  };
}

async function cachedManualPathStatus(
  manualPath: string,
): Promise<ManualPathStatus> {
  const now = Date.now();
  const cached = manualStatusCache.get(manualPath);
  if (cached !== undefined && cached.expiresAt > now) {
    return cached.value;
  }
  const value = inspectManualPathStatus(manualPath).catch((error) => {
    if (manualStatusCache.get(manualPath)?.value === value) {
      manualStatusCache.delete(manualPath);
    }
    throw error;
  });
  manualStatusCache.set(manualPath, {
    expiresAt: now + MANUAL_STATUS_TTL_MS,
    value,
  });
  return value;
}

export function clearManualPathStatusCache(manualPath?: string): void {
  if (manualPath === undefined) {
    manualStatusCache.clear();
    return;
  }
  manualStatusCache.delete(manualPath);
}

async function inspectManualPathStatus(
  manualPath: string,
): Promise<ManualPathStatus> {
  let stat: Awaited<ReturnType<typeof fs.stat>>;
  const finishRead = beginFilesystemRead();
  try {
    stat = await fs.stat(manualPath);
  } catch {
    return "untracked";
  } finally {
    finishRead();
  }
  if (!stat.isDirectory()) {
    return "untracked";
  }

  let result: Awaited<ReturnType<typeof runGit>>;
  try {
    result = await runGit(manualPath, GIT_STATUS_ARGS);
  } catch {
    return "untracked";
  }
  if (result.exitCode !== 0) {
    return "untracked";
  }
  return result.stdoutLines.length > 0 ? "dirty" : "clean";
}

async function gitManualPathDirtyChecker(
  manualPath: string,
): Promise<ManualPathDirtyCheckResult> {
  let stat: Awaited<ReturnType<typeof fs.stat>>;
  const finishRead = beginFilesystemRead();
  try {
    stat = await fs.stat(manualPath);
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) {
      return { dirty: false, statusLines: [] };
    }
    throw new Error(
      `Unable to inspect manual dependency path ${manualPath}: ${errorMessage(error)}`,
    );
  } finally {
    finishRead();
  }
  if (!stat.isDirectory()) {
    return { dirty: false, statusLines: [] };
  }

  const result = await runGit(manualPath, GIT_STATUS_ARGS);
  if (result.exitCode !== 0) {
    const details = [...result.stderrLines, ...result.stdoutLines]
      .join("\n")
      .trim();
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

async function readGitHeadCommit(
  cwd: string,
  dependencyName: string,
): Promise<string> {
  let result: Awaited<ReturnType<typeof runGit>>;
  try {
    result = await runGit(cwd, ["rev-parse", "--verify", "HEAD"]);
  } catch (error) {
    throw new Error(
      `Unable to resolve ${dependencyName} manual HEAD at ${cwd}: ${errorMessage(error)}`,
    );
  }
  if (result.exitCode !== 0) {
    const details = [...result.stderrLines, ...result.stdoutLines]
      .join("\n")
      .trim();
    throw new Error(
      `Unable to resolve ${dependencyName} manual HEAD at ${cwd}: ${
        details || `git exited with code ${result.exitCode}`
      }`,
    );
  }
  const commit = result.stdoutLines[0]?.trim();
  if (commit === undefined || commit === "") {
    throw new Error(`Unable to resolve ${dependencyName} manual HEAD at ${cwd}`);
  }
  return commit;
}

async function runGit(
  cwd: string,
  args: readonly string[],
): Promise<{
  readonly exitCode: number | null;
  readonly stdoutLines: string[];
  readonly stderrLines: string[];
}> {
  return new Promise((resolve, reject) => {
    const finishGit = beginGitProcess();
    const child = spawn("git", [...args], {
      cwd,
      shell: false,
    });
    let stdout: Buffer = Buffer.alloc(0);
    let stderr: Buffer = Buffer.alloc(0);
    child.stdout.on("data", (chunk: Buffer | string) => {
      stdout = appendBoundedOutput(stdout, chunk);
    });
    child.stderr.on("data", (chunk: Buffer | string) => {
      stderr = appendBoundedOutput(stderr, chunk);
    });
    child.on("error", (error) => {
      finishGit();
      reject(error);
    });
    child.on("close", (exitCode) => {
      finishGit();
      resolve({
        exitCode,
        stdoutLines: splitNonEmptyLines(stdout.toString("utf8")),
        stderrLines: splitNonEmptyLines(stderr.toString("utf8")),
      });
    });
  });
}

export function appendBoundedOutput(
  current: Buffer,
  chunk: Buffer | string,
): Buffer {
  const next = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
  if (next.length >= GIT_OUTPUT_LIMIT_BYTES) {
    return next.subarray(next.length - GIT_OUTPUT_LIMIT_BYTES);
  }
  const combined = Buffer.concat([current, next]);
  return combined.length <= GIT_OUTPUT_LIMIT_BYTES
    ? combined
    : combined.subarray(combined.length - GIT_OUTPUT_LIMIT_BYTES);
}

export async function mapWithConcurrency<T, R>(
  values: readonly T[],
  concurrency: number,
  worker: (value: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (!Number.isInteger(concurrency) || concurrency <= 0) {
    throw new Error("concurrency must be a positive integer");
  }
  const results = new Array<R>(values.length);
  let nextIndex = 0;
  const workers = Array.from(
    { length: Math.min(concurrency, values.length) },
    async () => {
      while (nextIndex < values.length) {
        const index = nextIndex;
        nextIndex += 1;
        results[index] = await worker(values[index], index);
      }
    },
  );
  await Promise.all(workers);
  return results;
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
      throw new Error(
        `Dependency ${name} is missing from active lock while updating ${filePath}`,
      );
    }
    if (
      typeof activeEntry.commit !== "string" ||
      activeEntry.commit.trim() === ""
    ) {
      throw new Error(
        `Dependency ${name} has no resolved commit in active lock`,
      );
    }
    next[name] = {
      ...templateEntry,
      commit: activeEntry.commit.trim(),
    };
  }
  return next;
}

function setJsonValue(
  text: string,
  propertyPath: (string | number)[],
  value: unknown,
): string {
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

async function writeLockText(filePath: string, text: string): Promise<void> {
  await atomicWriteText(filePath, ensureTrailingNewline(text));
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertSafeDependencyName(name: string): void {
  if (!isSafeDependencyName(name)) {
    throw new Error(`Invalid dependency name ${JSON.stringify(name)}`);
  }
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

function resolveManualPath(repoRoot: string, configuredPath: string): string {
  return isAbsolutePath(configuredPath)
    ? configuredPath
    : path.resolve(repoRoot, configuredPath);
}

function isAbsolutePath(value: string): boolean {
  return path.isAbsolute(value) || /^[A-Za-z]:[\\/]/.test(value);
}
