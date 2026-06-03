import * as assert from "assert";
import { EventEmitter } from "events";
import { PassThrough } from "stream";
import {
  ProcessLike,
  ProcessRunner,
  WorkflowOutput,
  runOfflineUpdate,
  runWorkflowFlag,
} from "../../workflowRunner";
import {
  pythonCommandForPlatform,
  workflowTerminalCommand,
} from "../../workflowCommands";

class MockProcess extends EventEmitter implements ProcessLike {
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
}

class MockOutput implements WorkflowOutput {
  readonly lines: Array<{ level: string; value: string }> = [];

  log(
    level: "info" | "success" | "warning" | "error" | "context",
    value: string,
  ): void {
    this.lines.push({ level, value });
  }
}

suite("workflow runner", () => {
  test("workflow terminal commands use Windows python launcher", () => {
    assert.strictEqual(
      workflowTerminalCommand("--init", "win32"),
      "python configs/source_root_workflow.py --init",
    );
    assert.strictEqual(pythonCommandForPlatform("win32"), "python");
  });

  test("workflow terminal commands use python3 off Windows", () => {
    assert.strictEqual(
      workflowTerminalCommand("--update", "linux"),
      "python3 configs/source_root_workflow.py --update",
    );
    assert.strictEqual(pythonCommandForPlatform("darwin"), "python3");
  });

  test("offline update runs Windows python workflow update from repo root", async () => {
    const repoRoot = "/tmp/freecm-runner";
    const output = new MockOutput();
    const calls: Array<{
      command: string;
      args: readonly string[];
      cwd: string;
    }> = [];
    const runner: ProcessRunner = {
      spawn(command, args, options) {
        calls.push({ command, args, cwd: options.cwd });
        const child = new MockProcess();
        queueMicrotask(() => child.emit("close", 0, null));
        return child;
      },
    };

    await runOfflineUpdate(repoRoot, output, runner, "win32");

    assert.deepStrictEqual(output.lines.slice(0, 2), [
      {
        level: "info",
        value: "python configs/source_root_workflow.py --update",
      },
      {
        level: "context",
        value: `cwd=${repoRoot}`,
      },
    ]);
    assert.deepStrictEqual(calls, [
      {
        command: "python",
        args: ["configs/source_root_workflow.py", "--update"],
        cwd: repoRoot,
      },
    ]);
  });

  test("offline update keeps python3 on non-Windows platforms", async () => {
    const repoRoot = "/tmp/freecm-runner";
    const output = new MockOutput();
    const calls: Array<{
      command: string;
      args: readonly string[];
      cwd: string;
    }> = [];
    const runner: ProcessRunner = {
      spawn(command, args, options) {
        calls.push({ command, args, cwd: options.cwd });
        const child = new MockProcess();
        queueMicrotask(() => child.emit("close", 0, null));
        return child;
      },
    };

    await runOfflineUpdate(repoRoot, output, runner, "linux");

    assert.deepStrictEqual(calls, [
      {
        command: "python3",
        args: ["configs/source_root_workflow.py", "--update"],
        cwd: repoRoot,
      },
    ]);
  });

  test("workflow flags run from repo root and log to FreeCM log output", async () => {
    const repoRoot = "/tmp/freecm-runner";
    const output = new MockOutput();
    const calls: Array<{
      command: string;
      args: readonly string[];
      cwd: string;
    }> = [];
    const runner: ProcessRunner = {
      spawn(command, args, options) {
        calls.push({ command, args, cwd: options.cwd });
        const child = new MockProcess();
        queueMicrotask(() => child.emit("close", 0, null));
        return child;
      },
    };

    await runWorkflowFlag(repoRoot, "--init", output, runner, "linux");

    assert.deepStrictEqual(calls, [
      {
        command: "python3",
        args: ["configs/source_root_workflow.py", "--init"],
        cwd: repoRoot,
      },
    ]);
    assert.deepStrictEqual(output.lines.slice(0, 2), [
      {
        level: "info",
        value: "python3 configs/source_root_workflow.py --init",
      },
      {
        level: "context",
        value: `cwd=${repoRoot}`,
      },
    ]);
  });

  test("offline update forwards stdout and stderr to terminal output levels", async () => {
    const repoRoot = "/tmp/freecm-runner";
    const output = new MockOutput();
    const runner: ProcessRunner = {
      spawn() {
        const child = new MockProcess();
        queueMicrotask(() => {
          child.stdout.write("updated\n");
          child.stderr.write("warning\n");
          child.emit("close", 0, null);
        });
        return child;
      },
    };

    await runOfflineUpdate(repoRoot, output, runner, "linux");

    assert.ok(
      output.lines.some(
        (line) => line.level === "info" && line.value === "updated",
      ),
    );
    assert.ok(
      output.lines.some(
        (line) => line.level === "warning" && line.value === "warning",
      ),
    );
  });
});
