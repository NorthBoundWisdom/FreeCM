import * as assert from "assert";
import * as vscode from "vscode";

import { CommandControllerHost } from "../../controllers/commandHost";
import { WorkflowController } from "../../controllers/workflowController";

suite("workflow controller", () => {
  test("queues terminal workflows without taking the launch gate", async () => {
    const folder = { name: "Host", fsPath: "/repo/Host" };
    const queued: string[][] = [];
    const logs: Array<{ level: string; message: string }> = [];
    let launchMutations = 0;
    const host = {
      workspaceState: {
        invalidateCache: () => undefined,
      },
      isLaunching: () => true,
      setLaunching: () => {
        launchMutations += 1;
      },
      setStatusBarLaunchCommand: () => undefined,
      refresh: async () => undefined,
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

    await new WorkflowController(host).runWorkflowCommand("--update");

    assert.deepStrictEqual(queued, [
      ["python3 configs/source_root_workflow.py --update"],
    ]);
    assert.strictEqual(launchMutations, 0);
    assert.ok(
      logs.some(
        ({ level, message }) =>
          level === "success" &&
          message ===
            "Queued configs/source_root_workflow.py --update",
      ),
    );
  });
});
