import * as assert from "assert";
import * as vscode from "vscode";

import { TerminalSessionManager } from "../../terminal/terminalSessionManager";

const folder = { name: "Host", fsPath: "/repo/Host" };

function createTerminal(
  sendText: (line: string) => void,
  executeCommand: (line: string) => vscode.TerminalShellExecution = () =>
    ({} as vscode.TerminalShellExecution),
): vscode.Terminal {
  return {
    show: () => undefined,
    sendText,
    shellIntegration: {
      cwd: undefined,
      executeCommand,
    },
  } as unknown as vscode.Terminal;
}

function deferred<T>(): {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

suite("terminal session manager", () => {
  test("does not create the log terminal for success or context logs", () => {
    const created: vscode.ExtensionTerminalOptions[] = [];
    const original = vscode.window.createTerminal;
    (
      vscode.window as { createTerminal: typeof vscode.window.createTerminal }
    ).createTerminal = ((options: vscode.ExtensionTerminalOptions) => {
      created.push(options);
      return { show() {} } as vscode.Terminal;
    }) as typeof vscode.window.createTerminal;

    try {
      const manager = new TerminalSessionManager();
      manager.logToTerminal("success", "Queued Config: Windows Debug");
      manager.logToTerminal("context", "PATH += C:\\Tools");
      manager.logToTerminal("info", "starting");
      assert.strictEqual(created.length, 0);
    } finally {
      (
        vscode.window as { createTerminal: typeof vscode.window.createTerminal }
      ).createTerminal = original;
    }
  });

  test("creates the log terminal only when a warning or error must be shown", () => {
    const created: vscode.ExtensionTerminalOptions[] = [];
    const original = vscode.window.createTerminal;
    (
      vscode.window as { createTerminal: typeof vscode.window.createTerminal }
    ).createTerminal = ((options: vscode.ExtensionTerminalOptions) => {
      created.push(options);
      return { show() {} } as vscode.Terminal;
    }) as typeof vscode.window.createTerminal;

    try {
      new TerminalSessionManager().logToTerminal(
        "warning",
        "Select Config before running Build.",
      );
      assert.strictEqual(created.length, 1);
      assert.strictEqual(created[0]?.name, "FreeCM Log");
      assert.ok(created[0]?.pty);
    } finally {
      (
        vscode.window as { createTerminal: typeof vscode.window.createTerminal }
      ).createTerminal = original;
    }
  });

  test("reveals the log terminal only for warnings and errors", () => {
    const manager = new TerminalSessionManager();
    const showArguments: Array<boolean | undefined> = [];
    const internal = manager as unknown as {
      logTerminal: vscode.Terminal | undefined;
    };
    internal.logTerminal = {
      show: (preserveFocus?: boolean) => {
        showArguments.push(preserveFocus);
      },
    } as vscode.Terminal;

    manager.logToTerminal("info", "starting");
    manager.logToTerminal("context", "PATH += /repo/tools");
    manager.logToTerminal("success", "queued");
    assert.deepStrictEqual(showArguments, []);

    manager.logToTerminal("warning", "check configuration");
    manager.logToTerminal("error", "command failed");
    assert.deepStrictEqual(showArguments, [true, true]);
  });

  test("waits for Windows bootstrap before sending the first command", async () => {
    const sent: string[] = [];
    const terminal = createTerminal((line) => sent.push(line));
    const manager = new TerminalSessionManager();
    const ready = deferred<void>();
    (
      manager as unknown as {
        windowsBootstrap: WeakMap<vscode.Terminal, Promise<void>>;
      }
    ).windowsBootstrap.set(terminal, ready.promise);

    const queued = manager.queueInFreeCMTerminal(folder, () => terminal, [
      "cmake --preset windows-debug",
    ]);
    await new Promise<void>((resolve) => setImmediate(resolve));
    assert.deepStrictEqual(sent, []);

    ready.resolve();
    await queued;
    assert.deepStrictEqual(sent, ["cmake --preset windows-debug"]);
  });

  test("sends the exact single command without a completion wrapper", async () => {
    const sent: string[] = [];
    const shellExecutions: string[] = [];
    const terminal = createTerminal(
      (line) => sent.push(line),
      (line) => {
        shellExecutions.push(line);
        return {} as vscode.TerminalShellExecution;
      },
    );

    await new TerminalSessionManager().queueInFreeCMTerminal(
      folder,
      () => terminal,
      ["cmake --preset mac_clang_release"],
    );

    assert.deepStrictEqual(sent, ["cmake --preset mac_clang_release"]);
    assert.deepStrictEqual(shellExecutions, []);
  });

  test("sends multi-step commands as one fail-closed shell sequence", async () => {
    const sent: string[] = [];
    const terminal = createTerminal((line) => sent.push(line));

    await new TerminalSessionManager().queueInFreeCMTerminal(
      folder,
      () => terminal,
      ["cmake --preset release", "cmake --build --preset release"],
    );

    const expectedSequence =
      process.platform === "win32"
        ? "cmake --preset release; if ($?) { cmake --build --preset release }"
        : "cmake --preset release && cmake --build --preset release";
    assert.deepStrictEqual(sent, [
      expectedSequence,
    ]);
  });

  test("serializes command delivery without waiting for command completion", async () => {
    const firstTerminal = deferred<vscode.Terminal>();
    const sent: string[] = [];
    const terminal = createTerminal((line) => sent.push(line));
    const manager = new TerminalSessionManager();
    let secondFactoryCalled = false;

    const first = manager.queueInFreeCMTerminal(
      folder,
      () => firstTerminal.promise,
      ["first"],
    );
    const second = manager.queueInFreeCMTerminal(
      folder,
      () => {
        secondFactoryCalled = true;
        return terminal;
      },
      ["second"],
    );

    await new Promise<void>((resolve) => setImmediate(resolve));
    assert.strictEqual(secondFactoryCalled, false);
    firstTerminal.resolve(terminal);
    await Promise.all([first, second]);

    assert.strictEqual(secondFactoryCalled, true);
    assert.deepStrictEqual(sent, ["first", "second"]);
  });

  test("does not create a terminal for an empty command", async () => {
    let factoryCalled = false;
    await new TerminalSessionManager().queueInFreeCMTerminal(
      folder,
      () => {
        factoryCalled = true;
        return createTerminal(() => undefined);
      },
      [],
    );

    assert.strictEqual(factoryCalled, false);
  });

  test("recreates a disposed terminal and retries the original command", async () => {
    const sent: string[] = [];
    const disposed = createTerminal(() => {
      throw new Error("Terminal has already been disposed");
    });
    const replacement = createTerminal((line) => sent.push(line));
    const manager = new TerminalSessionManager();
    const warnings: string[] = [];
    const internal = manager as unknown as {
      logToTerminal: (level: string, message: string) => void;
    };
    internal.logToTerminal = (level, message) => {
      if (level === "warning") {
        warnings.push(message);
      }
    };
    let calls = 0;

    await manager.queueInFreeCMTerminal(
      folder,
      () => {
        calls += 1;
        return calls === 1 ? disposed : replacement;
      },
      ["cmake --build --preset release"],
    );

    assert.strictEqual(calls, 2);
    assert.deepStrictEqual(sent, ["cmake --build --preset release"]);
    assert.deepStrictEqual(warnings, [
      "FreeCM terminal was already disposed; recreating it and retrying.",
    ]);
  });
});
