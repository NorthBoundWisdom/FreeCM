import { spawn } from "child_process";
import { Dirent } from "fs";
import * as fs from "fs/promises";
import * as path from "path";
import { withWorkspaceLock } from "./workspaceLock";

export interface GitWorkflowOutput {
  log(
    level: "info" | "success" | "warning" | "error" | "context",
    value: string,
  ): void;
}

export interface ProcessRunner {
  spawn(
    command: string,
    args: readonly string[],
    options: { cwd: string },
  ): ProcessLike;
}

export interface ProcessLike {
  readonly stdout?: NodeJS.ReadableStream | null;
  readonly stderr?: NodeJS.ReadableStream | null;
  on(event: "error", listener: (error: Error) => void): this;
  on(
    event: "close",
    listener: (code: number | null, signal: NodeJS.Signals | null) => void,
  ): this;
}

export interface SeedPullSummary {
  readonly succeeded: readonly string[];
  readonly skipped: readonly string[];
  readonly failed: readonly string[];
}

export const nodeProcessRunner: ProcessRunner = {
  spawn(command, args, options) {
    return spawn(command, [...args], {
      cwd: options.cwd,
      shell: false,
    });
  },
};

export async function pullWithRebaseIfClean(
  repoPath: string,
  label: string,
  output: GitWorkflowOutput,
  runner: ProcessRunner = nodeProcessRunner,
): Promise<void> {
  output.log("info", `Checking ${label} worktree before pull.`);
  output.log("context", `cwd=${repoPath}`);

  const status = await runGit(
    repoPath,
    ["status", "--porcelain=v1"],
    output,
    runner,
    {
      forwardOutput: false,
    },
  );
  if (status.code !== 0) {
    throw new Error(`${label} git status failed with exit code ${status.code}`);
  }

  const statusLines = splitNonEmptyLines(status.stdout);
  if (statusLines.length > 0) {
    output.log("error", `${label} worktree is dirty; pull stopped.`);
    for (const line of statusLines) {
      output.log("warning", `  ${line}`);
    }
    throw new Error(`${label} worktree is dirty.`);
  }

  const branchName = await resolvePullBranch(repoPath, output, runner);
  if (branchName === undefined) {
    const detachedBranch = await resolveDetachedBranch(
      repoPath,
      output,
      runner,
    );
    if (detachedBranch === undefined) {
      output.log(
        "warning",
        `Detached HEAD without a tracked remote branch; pull stopped.`,
      );
      throw new Error(`${label} detached HEAD has no tracked remote branch.`);
    }

    output.log(
      "info",
      `Detached HEAD; refreshing ${label} from origin/${detachedBranch}.`,
    );
    const fetch = await runGit(
      repoPath,
      ["fetch", "origin", detachedBranch],
      output,
      runner,
      {
        forwardOutput: true,
      },
    );
    if (fetch.code !== 0) {
      throw new Error(`${label} git fetch failed with exit code ${fetch.code}`);
    }

    const reset = await runGit(
      repoPath,
      ["reset", "--hard", `origin/${detachedBranch}`],
      output,
      runner,
      {
        forwardOutput: true,
      },
    );
    if (reset.code !== 0) {
      throw new Error(`${label} git reset failed with exit code ${reset.code}`);
    }
  } else {
    output.log("info", `Running git pull --rebase for ${label}.`);
    const pull = await runGit(repoPath, ["pull", "--rebase"], output, runner, {
      forwardOutput: true,
    });
    if (pull.code !== 0) {
      throw new Error(
        `${label} git pull --rebase failed with exit code ${pull.code}`,
      );
    }
  }
  output.log("success", `${label} is up to date.`);
}

export async function pullExistingSeedRepositories(
  repoRoot: string,
  output: GitWorkflowOutput,
  runner: ProcessRunner = nodeProcessRunner,
): Promise<SeedPullSummary> {
  return withWorkspaceLock(repoRoot, async () => {
    const seedRoot = path.join(repoRoot, "build", "dependency_seed_repos");
    const entries = await seedRepositoryEntries(seedRoot);
    if (entries.length === 0) {
      output.log("info", "No existing dependency seed repositories were found.");
      return emptySeedPullSummary();
    }

    const succeeded: string[] = [];
    const skipped: string[] = [];
    const failed: string[] = [];
    for (const entry of entries) {
      const repoPath = path.join(seedRoot, entry);
      output.log("info", `Checking ${entry} seed worktree before pull.`);
      output.log("context", `cwd=${repoPath}`);
      try {
        const status = await runGit(
          repoPath,
          ["status", "--porcelain=v1"],
          output,
          runner,
          { forwardOutput: false },
        );
        if (status.code !== 0) {
          failed.push(entry);
          output.log("error", `${entry} seed git status failed with exit code ${status.code}.`);
          continue;
        }

        const statusLines = splitNonEmptyLines(status.stdout);
        if (statusLines.length > 0) {
          skipped.push(entry);
          output.log("warning", `${entry} seed worktree is dirty; pull skipped.`);
          for (const line of statusLines) {
            output.log("warning", `  ${line}`);
          }
          continue;
        }

        output.log("info", `Running git pull --rebase for ${entry} seed.`);
        const pull = await runGit(
          repoPath,
          ["pull", "--rebase"],
          output,
          runner,
          { forwardOutput: true },
        );
        if (pull.code !== 0) {
          failed.push(entry);
          output.log(
            "error",
            `${entry} seed git pull --rebase failed with exit code ${pull.code}.`,
          );
          continue;
        }
        succeeded.push(entry);
        output.log("success", `${entry} seed is up to date.`);
      } catch (error) {
        failed.push(entry);
        output.log(
          "error",
          `${entry} seed pull failed: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
    }

    const summary = { succeeded, skipped, failed };
    logSeedPullSummary(summary, output);
    return summary;
  });
}

async function seedRepositoryEntries(seedRoot: string): Promise<string[]> {
  let entries: Dirent<string>[];
  try {
    entries = await fs.readdir(seedRoot, { withFileTypes: true });
  } catch (error) {
    if (isMissingPath(error)) {
      return [];
    }
    throw error;
  }

  const repositories: string[] = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }
    try {
      const gitMarker = await fs.lstat(path.join(seedRoot, entry.name, ".git"));
      if (gitMarker.isDirectory() || gitMarker.isFile()) {
        repositories.push(entry.name);
      }
    } catch (error) {
      if (!isMissingPath(error)) {
        throw error;
      }
    }
  }
  return repositories.sort(compareNames);
}

function logSeedPullSummary(
  summary: SeedPullSummary,
  output: GitWorkflowOutput,
): void {
  output.log(
    summary.failed.length === 0 ? "success" : "warning",
    `Pull Seeds summary: ${summary.succeeded.length} succeeded, ` +
      `${summary.skipped.length} skipped, ${summary.failed.length} failed.`,
  );
  for (const [label, names] of [
    ["Succeeded", summary.succeeded],
    ["Skipped", summary.skipped],
    ["Failed", summary.failed],
  ] as const) {
    if (names.length > 0) {
      output.log("context", `${label}: ${names.join(", ")}`);
    }
  }
}

function emptySeedPullSummary(): SeedPullSummary {
  return { succeeded: [], skipped: [], failed: [] };
}

function compareNames(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function isMissingPath(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    (error as NodeJS.ErrnoException).code === "ENOENT"
  );
}

async function runGit(
  cwd: string,
  args: readonly string[],
  output: GitWorkflowOutput,
  runner: ProcessRunner,
  options: { readonly forwardOutput: boolean },
): Promise<{
  readonly code: number | null;
  readonly stdout: string;
  readonly stderr: string;
}> {
  return new Promise((resolve, reject) => {
    const child = runner.spawn("git", args, { cwd });
    let stdout = "";
    let stderr = "";

    child.stdout?.on("data", (chunk: Buffer | string) => {
      const text = chunk.toString();
      stdout += text;
      if (options.forwardOutput) {
        output.log("info", text.trimEnd());
      }
    });
    child.stderr?.on("data", (chunk: Buffer | string) => {
      const text = chunk.toString();
      stderr += text;
      if (options.forwardOutput) {
        output.log("warning", text.trimEnd());
      }
    });
    child.on("error", reject);
    child.on("close", (code) => {
      resolve({ code, stdout, stderr });
    });
  });
}

async function resolvePullBranch(
  cwd: string,
  output: GitWorkflowOutput,
  runner: ProcessRunner,
): Promise<string | undefined> {
  const branch = await runGit(
    cwd,
    ["symbolic-ref", "-q", "--short", "HEAD"],
    output,
    runner,
    {
      forwardOutput: false,
    },
  );
  if (branch.code === 0 && branch.stdout.trim().length > 0) {
    return branch.stdout.trim();
  }
  return undefined;
}

async function resolveDetachedBranch(
  cwd: string,
  output: GitWorkflowOutput,
  runner: ProcessRunner,
): Promise<string | undefined> {
  const originHead = await runGit(
    cwd,
    ["symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
    output,
    runner,
    { forwardOutput: false },
  );
  if (originHead.code !== 0) {
    return undefined;
  }

  const match = originHead.stdout.trim().match(/^refs\/remotes\/origin\/(.+)$/);
  return match?.[1];
}

function splitNonEmptyLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0);
}
