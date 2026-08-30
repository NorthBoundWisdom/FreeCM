import { spawn } from "child_process";
import type { ChildProcess } from "child_process";
import * as path from "path";

import { cleanBuild } from "./cleanBuild";
import {
  pullExistingSeedRepositories,
  pullWithRebaseIfClean,
} from "./gitWorkflow";
import type { ProcessRunner } from "./gitWorkflow";
import {
  applyActiveDependencyToSample,
  manualAll,
  manualDependency,
  pinLatest,
  restoreDependencyPin,
  updateUsed,
  usePinned,
} from "./lockWorkflow";
import type { TerminalWorkflowAction } from "./terminal/terminalWorkflowCommand";
import { runOfflineUpdate } from "./workflowRunner";

type OutputLevel = "info" | "success" | "warning" | "error" | "context";

const ACTIONS = new Set<TerminalWorkflowAction>([
  "pull",
  "pull-seeds",
  "use-pinned",
  "pin-latest",
  "manual-all",
  "update-used",
  "apply-active-dependency-to-sample",
  "manual-dependency",
  "restore-dependency-pin",
  "clean-build",
]);

const output = {
  log(level: OutputLevel, value: string): void {
    const stream =
      level === "warning" || level === "error"
        ? process.stderr
        : process.stdout;
    for (const line of value.split(/\r?\n/)) {
      stream.write(`[FreeCM] ${line}\n`);
    }
  },
};

export async function runTerminalWorkflow(
  action: TerminalWorkflowAction,
  args: readonly string[],
  repoRoot: string = process.cwd(),
): Promise<void> {
  if (action === "pull") {
    requireArgumentCount(action, args, 0);
    await pullWithRebaseIfClean(
      repoRoot,
      path.basename(repoRoot),
      output,
      terminalProcessRunner(),
    );
    return;
  }
  if (action === "pull-seeds") {
    requireArgumentCount(action, args, 0);
    await pullExistingSeedRepositories(
      repoRoot,
      output,
      terminalProcessRunner(),
    );
    return;
  }
  if (action === "use-pinned") {
    requireArgumentCount(action, args, 0);
    output.log("info", "Use pinned: updating active lock.");
    await usePinned(repoRoot, { output });
    output.log("success", "Active lock now uses pinned dependencies.");
    return;
  }
  if (action === "manual-all") {
    requireArgumentCount(action, args, 0);
    output.log("info", "Manual all: updating active lock.");
    await manualAll(repoRoot, { output });
    output.log("success", "Active lock now uses manual seed paths.");
    return;
  }
  if (action === "pin-latest") {
    requireArgumentCount(action, args, 0);
    await runPinLatest(repoRoot);
    return;
  }
  if (action === "update-used") {
    requireArgumentCount(action, args, 0);
    output.log("info", "Update used: syncing active lock commits to template.");
    await updateUsed(repoRoot);
    output.log(
      "success",
      "Template lock now uses active lock dependency commits.",
    );
    return;
  }
  if (action === "apply-active-dependency-to-sample") {
    const dependency = requireDependencyArgument(action, args);
    output.log("info", `Apply active dependency to sample: ${dependency}`);
    await applyActiveDependencyToSample(repoRoot, dependency);
    output.log(
      "success",
      `Sample lock now uses the active ${dependency} commit.`,
    );
    return;
  }
  if (action === "manual-dependency") {
    const dependency = requireDependencyArgument(action, args);
    output.log("info", `Manual dependency: ${dependency}`);
    await manualDependency(repoRoot, dependency);
    output.log(
      "success",
      `Active lock now uses the ${dependency} manual seed path.`,
    );
    return;
  }
  if (action === "restore-dependency-pin") {
    const dependency = requireDependencyArgument(action, args);
    output.log("info", `Restore pinned dependency: ${dependency}`);
    await restoreDependencyPin(repoRoot, dependency);
    output.log("success", `Active lock restored the ${dependency} pin.`);
    return;
  }

  if (action === "clean-build") {
    requireArgumentCount(action, args, 0);
    output.log(
      "warning",
      "Clean build: removing build outputs except dependency repositories.",
    );
    const result = await cleanBuild(repoRoot);
    if (result.removed.length === 0) {
      output.log(
        "success",
        `Found no build outputs to clean in ${path.basename(repoRoot)}.`,
      );
    } else {
      output.log(
        "success",
        `Removed ${result.removed.length} build output item(s) in ${path.basename(repoRoot)}.`,
      );
    }
    return;
  }

  throw new Error(`Unsupported FreeCM terminal workflow action: ${action}`);
}

async function runPinLatest(repoRoot: string): Promise<void> {
  let activeChild: ChildProcess | undefined;
  let interrupted = false;
  const runner = terminalProcessRunner((child) => {
    activeChild = child;
    if (interrupted) {
      child.kill("SIGINT");
    }
    child.once("close", () => {
      if (activeChild === child) {
        activeChild = undefined;
      }
    });
  });
  const handleInterrupt = (): void => {
    interrupted = true;
    activeChild?.kill("SIGINT");
  };

  process.on("SIGINT", handleInterrupt);
  try {
    output.log("info", "Pin latest: switching active lock to latest.");
    await pinLatest(
      repoRoot,
      (root) => runOfflineUpdate(root, output, runner),
      { output },
    );
    if (interrupted) {
      throw new InterruptedWorkflowError("Pin latest interrupted.");
    }
    output.log("success", "Active lock pinned latest local seed commits.");
  } catch (error) {
    if (interrupted) {
      throw new InterruptedWorkflowError("Pin latest interrupted.");
    }
    throw error;
  } finally {
    process.off("SIGINT", handleInterrupt);
  }
}

function terminalProcessRunner(
  onSpawn: (child: ChildProcess) => void = () => undefined,
): ProcessRunner {
  return {
    spawn(command, args, options) {
      const child = spawn(command, [...args], {
        cwd: options.cwd,
        shell: false,
        stdio: ["inherit", "pipe", "pipe"],
      });
      onSpawn(child);
      return child;
    },
  };
}

function requireDependencyArgument(
  action: TerminalWorkflowAction,
  args: readonly string[],
): string {
  requireArgumentCount(action, args, 1);
  return args[0];
}

function requireArgumentCount(
  action: TerminalWorkflowAction,
  args: readonly string[],
  expected: number,
): void {
  if (args.length !== expected) {
    throw new Error(
      `${action} expected ${expected} argument(s), received ${args.length}.`,
    );
  }
}

class InterruptedWorkflowError extends Error {}

async function main(
  argv: readonly string[] = process.argv.slice(2),
): Promise<number> {
  const [rawAction, rawRepoRoot, ...args] = argv;
  if (rawAction === "--help") {
    process.stdout.write(
      "Usage: terminalWorkflowCli.js <action> <repo-root> [dependency]\n",
    );
    return 0;
  }
  if (!ACTIONS.has(rawAction as TerminalWorkflowAction)) {
    throw new Error(
      `Unknown FreeCM terminal workflow action: ${rawAction ?? "<missing>"}`,
    );
  }
  if (rawRepoRoot === undefined || rawRepoRoot.trim() === "") {
    throw new Error("FreeCM terminal workflow repo root is missing.");
  }
  await runTerminalWorkflow(
    rawAction as TerminalWorkflowAction,
    args,
    path.resolve(rawRepoRoot),
  );
  return 0;
}

if (require.main === module) {
  void main().then(
    (exitCode) => {
      process.exitCode = exitCode;
    },
    (error: unknown) => {
      output.log(
        error instanceof InterruptedWorkflowError ? "warning" : "error",
        error instanceof Error ? error.message : String(error),
      );
      process.exitCode = error instanceof InterruptedWorkflowError ? 130 : 1;
    },
  );
}
