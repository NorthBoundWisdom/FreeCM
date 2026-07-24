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

    assert.deepStrictEqual(sent, [
      "cmake --preset release && cmake --build --preset release",
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
