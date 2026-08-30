import * as assert from "assert";
import * as vscode from "vscode";

import { CommandControllerHost } from "../../controllers/commandHost";
import { LockModeController } from "../../controllers/lockModeController";
import { terminalWorkflowCommand } from "../../terminal/terminalWorkflowCommand";

suite("lock mode controller", () => {
  test("queues Pin latest in the FreeCM terminal", async () => {
    const { controller, queued, logs } = testController();

    await controller.runLockWorkflowCommand("pinLatest");

    assert.deepStrictEqual(queued, [
      [terminalWorkflowCommand("pin-latest", "/repo/Host")],
    ]);
    assert.deepStrictEqual(logs, [
      { level: "success", message: "Queued Pin latest" },
    ]);
  });

  test("queues dependency lock actions with a quoted argument", async () => {
    const { controller, queued, logs } = testController();

    await controller.runDependencyWorkflowCommand(
      "manualDependency",
      "Lib A",
    );

    assert.deepStrictEqual(queued, [
      [
        terminalWorkflowCommand(
          "manual-dependency",
          "/repo/Host",
          ["Lib A"],
        ),
      ],
    ]);
    assert.deepStrictEqual(logs, [
      { level: "success", message: "Queued Manual dependency: Lib A" },
    ]);
  });
});

function testController(): {
  readonly controller: LockModeController;
  readonly queued: string[][];
  readonly logs: Array<{ level: string; message: string }>;
} {
  const folder = { name: "Host", fsPath: "/repo/Host" };
  const queued: string[][] = [];
  const logs: Array<{ level: string; message: string }> = [];
  const host = {
    workspaceState: {
      invalidateCache: () => undefined,
    },
    resolveTargetFolderWithCapability: async () => folder,
    terminalForFolder: async () => ({} as vscode.Terminal),
    queueInFreeCMTerminal: async (
      _folder: typeof folder,
      _terminalFactory: () => Promise<vscode.Terminal>,
      lines: string[],
    ) => {
      queued.push(lines);
    },
    logToTerminal: (level: string, message: string) => {
      logs.push({ level, message });
    },
    finishTerminalLogGroup: () => undefined,
  } as unknown as CommandControllerHost;

  return {
    controller: new LockModeController(host),
    queued,
    logs,
  };
}
