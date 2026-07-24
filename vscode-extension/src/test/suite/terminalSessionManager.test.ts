import * as assert from "assert";
import { spawnSync } from "child_process";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
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

function managerWithCompletion(markerPath: string): TerminalSessionManager {
  return new TerminalSessionManager({
    createCompletion: async (line) => ({
      markerPath,
      command: `record ${line}`,
    }),
    completionPollIntervalMs: 1,
  });
}

suite("terminal session manager", () => {
  test("waits for a completion marker before finishing a command", async () => {
    const markerDirectory = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-terminal-completion-"),
    );
    const markerPath = path.join(markerDirectory, "completion.status");
    const manager = managerWithCompletion(markerPath);
    const logs = captureCommandLogs(manager);
    const commands: string[] = [];
    const terminal = createTerminal((line) => {
      commands.push(line);
      return {} as vscode.TerminalShellExecution;
    });

    try {
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
      assert.deepStrictEqual(commands, [
        "record python3 configs/source_root_workflow.py --init",
      ]);
      assert.deepStrictEqual(logs, []);
      assert.strictEqual(completed, false);

      await fs.writeFile(markerPath, "0\n", "utf8");
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
    } finally {
      await fs.rm(markerDirectory, { recursive: true, force: true });
    }
  });

  test("runs terminal command steps in one fail-closed shell sequence", async () => {
    const markerDirectory = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-terminal-completion-"),
    );
    const markerPath = path.join(markerDirectory, "completion.status");
    const manager = managerWithCompletion(markerPath);
    const logs = captureCommandLogs(manager);
    const commands: string[] = [];
    const lines = ["cmake --preset release", "cmake --build --preset release"];
    const terminal = createTerminal((line) => {
      commands.push(line);
      return {} as vscode.TerminalShellExecution;
    });

    try {
      const running = manager.executeInFreeCMTerminal(
        { name: "Host", fsPath: "/repo/Host" },
        "Build: Release",
        () => terminal,
        lines,
      );

      await flushMicrotasks();
      assert.deepStrictEqual(commands, [
        `record ${terminalCommandSequence(lines)}`,
      ]);
      await fs.writeFile(markerPath, "0\n", "utf8");
      assert.deepStrictEqual(await running, {
        status: "success",
        exitCode: 0,
      });

      assert.deepStrictEqual(logs, [
        { level: "success", message: "Finished Build: Release (exit 0)" },
      ]);
    } finally {
      await fs.rm(markerDirectory, { recursive: true, force: true });
    }
  });

  test("reports a marker-recorded failure for a multi-step command", async () => {
    const markerDirectory = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-terminal-completion-"),
    );
    const markerPath = path.join(markerDirectory, "completion.status");
    const manager = managerWithCompletion(markerPath);
    const logs = captureCommandLogs(manager);
    const commands: string[] = [];
    const lines = ["cmake --preset release", "cmake --build --preset release"];
    const terminal = createTerminal((line) => {
      commands.push(line);
      return {} as vscode.TerminalShellExecution;
    });

    try {
      const running = manager.executeInFreeCMTerminal(
        { name: "Host", fsPath: "/repo/Host" },
        "Config: Release",
        () => terminal,
        lines,
      );

      await flushMicrotasks();
      await fs.writeFile(markerPath, "2\n", "utf8");
      assert.deepStrictEqual(await running, {
        status: "failure",
        exitCode: 2,
      });
      assert.deepStrictEqual(commands, [
        `record ${terminalCommandSequence(lines)}`,
      ]);
      assert.deepStrictEqual(logs, [
        { level: "error", message: "Finished Config: Release (exit 2)" },
      ]);
    } finally {
      await fs.rm(markerDirectory, { recursive: true, force: true });
    }
  });

  test("uses the completion marker when shell integration does not report an end event", async () => {
    const markerDirectory = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-terminal-completion-"),
    );
    const markerPath = path.join(markerDirectory, "completion.status");
    const manager = managerWithCompletion(markerPath);
    const logs = captureCommandLogs(manager);
    const commands: string[] = [];
    const lines = ["cmake --build --preset release", "./app"];
    const terminal = createTerminal((line) => {
      commands.push(line);
      return {} as vscode.TerminalShellExecution;
    });

    try {
      const running = manager.executeInFreeCMTerminal(
        { name: "Host", fsPath: "/repo/Host" },
        "Run: App",
        () => terminal,
        lines,
      );
      await flushMicrotasks();
      assert.deepStrictEqual(commands, [
        `record ${terminalCommandSequence(lines)}`,
      ]);
      await fs.writeFile(markerPath, "0\n", "utf8");

      assert.deepStrictEqual(await running, { status: "success", exitCode: 0 });
      assert.deepStrictEqual(logs, [
        { level: "success", message: "Finished Run: App (exit 0)" },
      ]);
    } finally {
      await fs.rm(markerDirectory, { recursive: true, force: true });
    }
  });

  test("uses the completion marker when shell integration is unavailable", async () => {
    const markerDirectory = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-terminal-completion-"),
    );
    const markerPath = path.join(markerDirectory, "completion.status");
    const manager = managerWithCompletion(markerPath);
    const logs = captureCommandLogs(manager);
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

    const running = manager.executeInFreeCMTerminal(
      { name: "Host", fsPath: "/repo/Host" },
      "Run: App",
      () => terminal,
      lines,
    );
    let completed = false;
    void running.then(() => {
      completed = true;
    });

    await flushMicrotasks();
    assert.deepStrictEqual(commands, [`record ${terminalCommandSequence(lines)}`]);
    assert.strictEqual(completed, false);

    await fs.writeFile(markerPath, "0\n", "utf8");
    assert.deepStrictEqual(await running, { status: "success", exitCode: 0 });
    assert.deepStrictEqual(logs, [
      { level: "success", message: "Finished Run: App (exit 0)" },
    ]);
    await fs.rm(markerDirectory, { recursive: true, force: true });
  });

  test("uses the default marker command when shell integration is unavailable", async () => {
    if (process.platform === "win32") {
      return;
    }
    const manager = new TerminalSessionManager({ completionPollIntervalMs: 1 });
    const logs = captureCommandLogs(manager);
    const terminal = {
      show: () => undefined,
      sendText: (line: string) => {
        const result = spawnSync("/bin/sh", ["-c", line], {
          encoding: "utf8",
        });
        assert.strictEqual(result.status, 0, result.stderr);
      },
    } as unknown as vscode.Terminal;
    const internal = manager as unknown as {
      waitForShellIntegration: () => Promise<undefined>;
    };
    internal.waitForShellIntegration = async () => undefined;

    assert.deepStrictEqual(
      await manager.executeInFreeCMTerminal(
        { name: "Host", fsPath: "/repo/Host" },
        "Config: Release",
        () => terminal,
        ["true"],
      ),
      { status: "success", exitCode: 0 },
    );
    assert.deepStrictEqual(logs, [
      { level: "success", message: "Finished Config: Release (exit 0)" },
    ]);
  });

  test("uses the default marker command through shell integration", async () => {
    if (process.platform === "win32") {
      return;
    }
    const manager = new TerminalSessionManager({ completionPollIntervalMs: 1 });
    const logs = captureCommandLogs(manager);
    const terminal = createTerminal((line) => {
      const result = spawnSync("/bin/sh", ["-c", line], {
        encoding: "utf8",
      });
      assert.strictEqual(result.status, 0, result.stderr);
      return {} as vscode.TerminalShellExecution;
    });

    assert.deepStrictEqual(
      await manager.executeInFreeCMTerminal(
        { name: "Host", fsPath: "/repo/Host" },
        "Config: Release",
        () => terminal,
        ["true"],
      ),
      { status: "success", exitCode: 0 },
    );
    assert.deepStrictEqual(logs, [
      { level: "success", message: "Finished Config: Release (exit 0)" },
    ]);
  });

  test("fails closed when a terminal closes before completion", async () => {
    const markerDirectory = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-terminal-completion-"),
    );
    const markerPath = path.join(markerDirectory, "completion.status");
    const manager = managerWithCompletion(markerPath);
    captureCommandLogs(manager);
    const terminal = {
      show: () => undefined,
      sendText: () => undefined,
    } as unknown as vscode.Terminal;
    const internal = manager as unknown as {
      waitForShellIntegration: () => Promise<undefined>;
    };
    internal.waitForShellIntegration = async () => undefined;

    const running = manager.executeInFreeCMTerminal(
      { name: "Host", fsPath: "/repo/Host" },
      "Run: App",
      () => terminal,
      ["./app"],
    );
    await flushMicrotasks();
    manager.handleTerminalClosed(terminal);

    assert.deepStrictEqual(await running, { status: "unknown" });
    await fs.rm(markerDirectory, { recursive: true, force: true });
  });

  test("releases a pending command when its replaced terminal closes", async () => {
    const markerDirectory = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-terminal-completion-"),
    );
    const markerPath = path.join(markerDirectory, "completion.status");
    const manager = managerWithCompletion(markerPath);
    captureCommandLogs(manager);
    const terminal = createTerminal(
      () => ({} as vscode.TerminalShellExecution),
    );
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
    await fs.rm(markerDirectory, { recursive: true, force: true });
  });
});
