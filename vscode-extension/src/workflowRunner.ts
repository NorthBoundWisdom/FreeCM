import { spawn } from "child_process";
import { TerminalLogLevel } from "./terminalLogger";
import { workflowInvocation } from "./workflowCommands";

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

export interface WorkflowOutput {
  log(level: TerminalLogLevel, value: string): void;
}

export const nodeProcessRunner: ProcessRunner = {
  spawn(command, args, options) {
    return spawn(command, [...args], {
      cwd: options.cwd,
      shell: false,
    });
  },
};

export async function runOfflineUpdate(
  repoRoot: string,
  output: WorkflowOutput,
  runner: ProcessRunner = nodeProcessRunner,
  platform: string = process.platform,
): Promise<void> {
  const { command, args } = workflowInvocation("--update", platform);
  output.log("info", `${command} ${args.join(" ")}`);
  output.log("context", `cwd=${repoRoot}`);

  await new Promise<void>((resolve, reject) => {
    const child = runner.spawn(command, args, { cwd: repoRoot });

    child.stdout?.on("data", (chunk: Buffer | string) => {
      output.log("info", chunk.toString().trimEnd());
    });
    child.stderr?.on("data", (chunk: Buffer | string) => {
      output.log("warning", chunk.toString().trimEnd());
    });
    child.on("error", reject);
    child.on("close", (code, signal) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(
        new Error(
          `Offline update failed with ${signal === null ? `exit code ${code}` : `signal ${signal}`}`,
        ),
      );
    });
  });
}
