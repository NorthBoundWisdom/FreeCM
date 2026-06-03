import * as fs from "fs/promises";
import * as path from "path";

const BUILD_DIR_NAME = "build";
const PRESERVED_BUILD_CHILDREN = new Set([
  "dependency_seed_repos",
  "dependency_source_roots",
]);

export interface CleanBuildResult {
  readonly removed: readonly string[];
  readonly preserved: readonly string[];
}

export async function cleanBuild(repoRoot: string): Promise<CleanBuildResult> {
  const buildDir = path.join(repoRoot, BUILD_DIR_NAME);
  let buildStat;
  try {
    buildStat = await fs.lstat(buildDir);
  } catch (error) {
    if (isNotFoundError(error)) {
      return { removed: [], preserved: [] };
    }
    throw new Error(`Unable to inspect ${buildDir}: ${errorMessage(error)}`);
  }

  if (buildStat.isSymbolicLink()) {
    throw new Error(`Refusing to clean symlinked build directory: ${buildDir}`);
  }
  if (!buildStat.isDirectory()) {
    throw new Error(`Refusing to clean non-directory build path: ${buildDir}`);
  }

  const removed: string[] = [];
  const preserved: string[] = [];
  for (const entry of await fs.readdir(buildDir, { withFileTypes: true })) {
    const label = path.posix.join(BUILD_DIR_NAME, entry.name);
    if (PRESERVED_BUILD_CHILDREN.has(entry.name)) {
      preserved.push(label);
      continue;
    }
    const childPath = path.join(buildDir, entry.name);
    assertBuildChild(repoRoot, childPath);
    await fs.rm(childPath, { force: true, recursive: true });
    removed.push(label);
  }

  return {
    removed: removed.sort(),
    preserved: preserved.sort(),
  };
}

function assertBuildChild(repoRoot: string, childPath: string): void {
  const resolvedRepoRoot = path.resolve(repoRoot);
  const resolvedBuildDir = path.join(resolvedRepoRoot, BUILD_DIR_NAME);
  const resolvedChild = path.resolve(childPath);
  if (path.dirname(resolvedChild) !== resolvedBuildDir) {
    throw new Error(
      `Refusing to remove path outside build directory: ${childPath}`,
    );
  }
}

function isNotFoundError(error: unknown): boolean {
  return isNodeError(error) && error.code === "ENOENT";
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
