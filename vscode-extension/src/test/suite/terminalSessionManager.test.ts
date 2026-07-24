import * as assert from "assert";
import * as vscode from "vscode";

import { TerminalLogLevel } from "../../terminalLogger";
import { terminalCommandSequence } from "../../terminal/terminalRuntime";
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

function executionWithCompletionStream(): {
  readonly execution: vscode.TerminalShellExecution;
  readonly finish: () => void;
} {
  let finish: () => void = () => undefined;
  const completed = new Promise<void>((resolve) => {
    finish = resolve;
  });
  return {
    execution: {
      read: () =>
        (async function* (): AsyncIterable<string> {
          await completed;
        })(),
    } as unknown as vscode.TerminalShellExecution,
    finish,
  };
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

  test("runs terminal command steps in one fail-closed shell sequence", async () => {
    const manager = new TerminalSessionManager();
    const logs = captureCommandLogs(manager);
    const commands: string[] = [];
    const execution = {} as vscode.TerminalShellExecution;
    const lines = ["cmake --preset release", "cmake --build --preset release"];
    const terminal = createTerminal((line) => {
      commands.push(line);
      return execution;
    });

    const running = manager.executeInFreeCMTerminal(
      { name: "Host", fsPath: "/repo/Host" },
      "Build: Release",
      () => terminal,
      lines,
    );

    await flushMicrotasks();
    assert.deepStrictEqual(commands, [terminalCommandSequence(lines)]);

    manager.handleTerminalShellExecutionEnded({
      execution,
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

  test("reports failure for a fail-closed multi-step command", async () => {
    const manager = new TerminalSessionManager();
    const logs = captureCommandLogs(manager);
    const commands: string[] = [];
    const execution = {} as vscode.TerminalShellExecution;
    const lines = ["cmake --preset release", "cmake --build --preset release"];
    const terminal = createTerminal((line) => {
      commands.push(line);
      return execution;
    });

    const running = manager.executeInFreeCMTerminal(
      { name: "Host", fsPath: "/repo/Host" },
      "Config: Release",
      () => terminal,
      lines,
    );

    await flushMicrotasks();
    manager.handleTerminalShellExecutionEnded({
      execution,
      exitCode: 2,
    } as vscode.TerminalShellExecutionEndEvent);

    assert.deepStrictEqual(await running, {
      status: "failure",
      exitCode: 2,
    });
    assert.deepStrictEqual(commands, [terminalCommandSequence(lines)]);
    assert.deepStrictEqual(logs, [
      { level: "error", message: "Finished Config: Release (exit 2)" },
    ]);
  });

  test("keeps all steps when output ends without an end event", async () => {
    const manager = new TerminalSessionManager();
    const logs = captureCommandLogs(manager);
    const { execution, finish } = executionWithCompletionStream();
    const commands: string[] = [];
    const lines = ["cmake --build --preset release", "./app"];
    const terminal = createTerminal((line) => {
      commands.push(line);
      return execution;
    });

    const running = manager.executeInFreeCMTerminal(
      { name: "Host", fsPath: "/repo/Host" },
      "Run: App",
      () => terminal,
      lines,
    );
    await flushMicrotasks();
    assert.deepStrictEqual(commands, [terminalCommandSequence(lines)]);
    finish();

    assert.deepStrictEqual(await running, { status: "unknown" });
    assert.deepStrictEqual(logs, [
      { level: "info", message: "Finished Run: App" },
    ]);
  });

  test("sends fallback steps as one fail-closed shell sequence", async () => {
    const manager = new TerminalSessionManager();
    captureCommandLogs(manager);
    const commands: string[] = [];
    const lines = ["cmake --build --preset release", "./app"];
    const terminal = {
      show: () => undefined,
      sendText: (line: string) => {
        commands.push(line);
      },
    } as unknown as vscode.Terminal;
    const internal = manager as unknown as {
      waitForShellIntegration: () => Promise<undefined>;
    };
    internal.waitForShellIntegration = async () => undefined;

    const outcome = await manager.executeInFreeCMTerminal(
      { name: "Host", fsPath: "/repo/Host" },
      "Run: App",
      () => terminal,
      lines,
    );

    assert.deepStrictEqual(outcome, { status: "unknown" });
    assert.deepStrictEqual(commands, [terminalCommandSequence(lines)]);
  });

  test("releases a pending command when its replaced terminal closes", async () => {
    const manager = new TerminalSessionManager();
    captureCommandLogs(manager);
    const execution = {} as vscode.TerminalShellExecution;
    const terminal = createTerminal(() => execution);
    const replacement = createTerminal(
      () => ({} as vscode.TerminalShellExecution),
    );
    const internal = manager as unknown as {
      terminal: vscode.Terminal | undefined;
    };
    internal.terminal = terminal;

    const running = manager.executeInFreeCMTerminal(
      { name: "Host", fsPath: "/repo/Host" },
      "Run: App",
      () => terminal,
      ["./app"],
    );
    await flushMicrotasks();
    internal.terminal = replacement;
    manager.handleTerminalClosed(terminal);

    assert.deepStrictEqual(await running, { status: "unknown" });
    assert.strictEqual(internal.terminal, replacement);
  });
});
