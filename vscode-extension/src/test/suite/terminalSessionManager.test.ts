import * as assert from "assert";
import * as vscode from "vscode";

import { TerminalLogLevel } from "../../terminalLogger";
import { TerminalSessionManager } from "../../terminal/terminalSessionManager";

interface CommandLog {
  readonly level: TerminalLogLevel;
  readonly message: string;
}

function createTerminal(
  executeCommand: (line: string) => vscode.TerminalShellExecution,
): vscode.Terminal {
  return {
    show: () => undefined,
    shellIntegration: {
      cwd: undefined,
      executeCommand,
    },
  } as unknown as vscode.Terminal;
}

function captureCommandLogs(manager: TerminalSessionManager): CommandLog[] {
  const logs: CommandLog[] = [];
  const internal = manager as unknown as {
    logToTerminal: (level: TerminalLogLevel, message: string) => void;
    finishTerminalLogGroup: () => void;
  };
  internal.logToTerminal = (level, message) => {
    logs.push({ level, message });
  };
  internal.finishTerminalLogGroup = () => undefined;
  return logs;
}

async function flushMicrotasks(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve));
}

suite("terminal session manager", () => {
  test("waits for shell execution completion before finishing a command", async () => {
    const manager = new TerminalSessionManager();
    const logs = captureCommandLogs(manager);
    const commands: string[] = [];
    const execution = {} as vscode.TerminalShellExecution;
    const terminal = createTerminal((line) => {
      commands.push(line);
      return execution;
    });

    const running = manager.executeInFreeCMTerminal(
      { name: "Host", fsPath: "/repo/Host" },
      "configs/source_root_workflow.py --init",
      () => terminal,
      ["python3 configs/source_root_workflow.py --init"],
    );
    let completed = false;
    void running.then(() => {
      completed = true;
    });

    await flushMicrotasks();
    assert.deepStrictEqual(commands, ["python3 configs/source_root_workflow.py --init"]);
    assert.deepStrictEqual(logs, []);
    assert.strictEqual(completed, false);

    manager.handleTerminalShellExecutionEnded({
      execution,
      exitCode: 0,
    } as vscode.TerminalShellExecutionEndEvent);
    assert.deepStrictEqual(await running, {
      status: "success",
      exitCode: 0,
    });

    assert.deepStrictEqual(logs, [
      {
        level: "success",
        message: "Finished configs/source_root_workflow.py --init (exit 0)",
      },
    ]);
  });

  test("runs terminal command steps serially", async () => {
    const manager = new TerminalSessionManager();
    const logs = captureCommandLogs(manager);
    const commands: string[] = [];
    const firstExecution = {} as vscode.TerminalShellExecution;
    const secondExecution = {} as vscode.TerminalShellExecution;
    const executions = [firstExecution, secondExecution];
    const terminal = createTerminal((line) => {
      commands.push(line);
      const execution = executions.shift();
      assert.ok(execution);
      return execution;
    });

    const running = manager.executeInFreeCMTerminal(
      { name: "Host", fsPath: "/repo/Host" },
      "Build: Release",
      () => terminal,
      ["cmake --preset release", "cmake --build --preset release"],
    );

    await flushMicrotasks();
    assert.deepStrictEqual(commands, ["cmake --preset release"]);

    manager.handleTerminalShellExecutionEnded({
      execution: firstExecution,
      exitCode: 0,
    } as vscode.TerminalShellExecutionEndEvent);
    await flushMicrotasks();
    assert.deepStrictEqual(commands, [
      "cmake --preset release",
      "cmake --build --preset release",
    ]);

    manager.handleTerminalShellExecutionEnded({
      execution: secondExecution,
      exitCode: 0,
    } as vscode.TerminalShellExecutionEndEvent);
    assert.deepStrictEqual(await running, {
      status: "success",
      exitCode: 0,
    });

    assert.deepStrictEqual(logs, [
      { level: "success", message: "Finished Build: Release (exit 0)" },
    ]);
  });

  test("stops a multi-step command after the first failing step", async () => {
    const manager = new TerminalSessionManager();
    const logs = captureCommandLogs(manager);
    const commands: string[] = [];
    const firstExecution = {} as vscode.TerminalShellExecution;
    const terminal = createTerminal((line) => {
      commands.push(line);
      return firstExecution;
    });

    const running = manager.executeInFreeCMTerminal(
      { name: "Host", fsPath: "/repo/Host" },
      "Config: Release",
      () => terminal,
      ["cmake --preset release", "cmake --build --preset release"],
    );

    await flushMicrotasks();
    manager.handleTerminalShellExecutionEnded({
      execution: firstExecution,
      exitCode: 2,
    } as vscode.TerminalShellExecutionEndEvent);

    assert.deepStrictEqual(await running, {
      status: "failure",
      exitCode: 2,
    });
    assert.deepStrictEqual(commands, ["cmake --preset release"]);
    assert.deepStrictEqual(logs, [
      { level: "error", message: "Finished Config: Release (exit 2)" },
    ]);
  });
});
