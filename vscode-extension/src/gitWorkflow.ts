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

  output.log("info", `Running git pull --rebase for ${label}.`);
  const pull = await runGit(repoPath, ["pull", "--rebase"], output, runner, {
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

function splitNonEmptyLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0);
}
