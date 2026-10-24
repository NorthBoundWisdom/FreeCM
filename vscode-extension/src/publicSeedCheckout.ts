import { execFile } from "child_process";
import * as fs from "fs/promises";
import * as path from "path";
import { promisify } from "util";
import { realDependencyPath, resolveDependencyPath } from "./dependencyPaths";
import { DependencyEntry } from "./lockSchemaValidation";
import { withDirectoryLock } from "./workspaceLock";

const run = promisify(execFile);

async function localGit(cwd: string, args: readonly string[]): Promise<string> {
  const result = await run("git", [
    "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
    "-c", "filter.lfs.process=", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.required=false",
    ...args,
  ], {
    cwd,
    env: { ...process.env, GIT_TERMINAL_PROMPT: "0", GIT_LFS_SKIP_SMUDGE: "1", GIT_NO_LAZY_FETCH: "1" },
    maxBuffer: 4 * 1024 * 1024,
  });
  return result.stdout.trim();
}

export async function prepareManualSeedCheckout(
  repoRoot: string, name: string, entry: DependencyEntry,
): Promise<void> {
  if (entry.disabled || !entry.publicSeedPath) return;
  const target = path.join(repoRoot, "build", "dependency_seed_repos", name);
  let targetExists = false;
  try {
    await fs.lstat(target);
    targetExists = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  if (targetExists) {
    // Existing editable checkouts (including dirty ones) are used as-is.
    const top = await localGit(target, ["rev-parse", "--show-toplevel"]);
    if (realDependencyPath(top) !== realDependencyPath(target)) {
      throw new Error(`Manual seed path is not a checkout root: ${target}`);
    }
    return;
  }

  const source = realDependencyPath(resolveDependencyPath(repoRoot, entry.publicSeedPath));
  const lockPath = path.join(path.dirname(source), `.${path.basename(source)}.freecm-seed.lock`);
  await withDirectoryLock(lockPath, async () => {
    const top = await localGit(source, ["rev-parse", "--show-toplevel"]);
    if (realDependencyPath(top) !== source) throw new Error(`publicSeedPath is not a checkout root: ${source}`);
    const remote = await localGit(source, ["config", "--get", "remote.origin.url"]);
    if (remote !== entry.remote) throw new Error(`publicSeedPath remote mismatch for ${name}: ${source}`);
    if (await localGit(source, ["status", "--porcelain=v1", "--untracked-files=all"])) {
      throw new Error(`publicSeedPath is dirty for ${name}: ${source}`);
    }
    await fs.mkdir(path.dirname(target), { recursive: true });
    const staging = await fs.mkdtemp(path.join(path.dirname(target), `.${name}-manual-`));
    try {
      // --local requires a filesystem source; --no-hardlinks and --dissociate
      // create independent objects without copying another host's worktree registry.
      await localGit(repoRoot, ["clone", "--local", "--no-hardlinks", "--dissociate", "--no-checkout", "--", source, staging]);
      await localGit(staging, ["config", "remote.origin.url", entry.remote]);
      await localGit(staging, ["reset", "--hard", "HEAD"]);
      await fs.rename(staging, target);
    } finally {
      await fs.rm(staging, { recursive: true, force: true });
    }
  }, {
    timeoutMs: 600_000,
    onWait: (waitingPath) => process.stdout.write(`[FreeCM] Waiting for shared seed lock: ${waitingPath}\n`),
  });
}
