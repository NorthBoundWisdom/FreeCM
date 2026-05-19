import { spawn } from "child_process";

export interface GitWorkflowOutput {
  log(level: "info" | "success" | "warning" | "error" | "context", value: string): void;
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
  on(event: "close", listener: (code: number | null, signal: NodeJS.Signals | null) => void): this;
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

  const status = await runGit(repoPath, ["status", "--porcelain=v1"], output, runner, {
    forwardOutput: false,
  });
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
  const pullArgs =
    branchName === undefined ? ["pull", "--rebase"] : ["pull", "--rebase", "origin", branchName];
  output.log(
    "info",
    branchName === undefined
      ? `Running git pull --rebase for ${label}.`
      : `Running git pull --rebase origin ${branchName} for ${label}.`,
  );
  const pull = await runGit(repoPath, pullArgs, output, runner, {
    forwardOutput: true,
  });
  if (pull.code !== 0) {
    throw new Error(`${label} git pull --rebase failed with exit code ${pull.code}`);
  }
  output.log("success", `${label} is up to date.`);
}

async function runGit(
  cwd: string,
  args: readonly string[],
  output: GitWorkflowOutput,
  runner: ProcessRunner,
  options: { readonly forwardOutput: boolean },
): Promise<{ readonly code: number | null; readonly stdout: string; readonly stderr: string }> {
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
  const branch = await runGit(cwd, ["symbolic-ref", "-q", "--short", "HEAD"], output, runner, {
    forwardOutput: false,
  });
  if (branch.code === 0 && branch.stdout.trim().length > 0) {
    return undefined;
  }
  return await resolveDetachedPullBranch(cwd, runner);
}

export async function resolveDetachedPullBranch(
  cwd: string,
  runner: ProcessRunner = nodeProcessRunner,
): Promise<string | undefined> {
  const originHead = await runGit(
    cwd,
    ["symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
    { log() {} },
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
