import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import { CommandControllerHost } from "../../controllers/commandHost";
import { WorkflowController } from "../../controllers/workflowController";
import { TerminalSessionManager } from "../../terminal/terminalSessionManager";

function createTerminal(): vscode.Terminal {
  return {
    show: () => undefined,
    shellIntegration: {
      cwd: undefined,
      executeCommand: () => ({} as vscode.TerminalShellExecution),
    },
  } as unknown as vscode.Terminal;
}

async function flushMicrotasks(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve));
}

suite("workflow controller", () => {
  test("keeps the launch gate until the terminal command finishes", async () => {
    const folder = { name: "Host", fsPath: "/repo/Host" };
    const terminal = createTerminal();
    const markerDirectory = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-workflow-controller-"),
    );
    const markerPath = path.join(markerDirectory, "completion.status");
    const terminalSession = new TerminalSessionManager({
      createCompletion: async (line) => ({
        markerPath,
        command: `record ${line}`,
      }),
      completionPollIntervalMs: 1,
    });
    const terminalSessionInternal = terminalSession as unknown as {
      logToTerminal: () => void;
      finishTerminalLogGroup: () => void;
    };
    terminalSessionInternal.logToTerminal = () => undefined;
    terminalSessionInternal.finishTerminalLogGroup = () => undefined;
    let launching = false;
    const logs: Array<{ level: string; message: string }> = [];
    const host = {
      workspaceState: {
        invalidateCache: () => undefined,
      },
      isLaunching: () => launching,
      setLaunching: (value: boolean) => {
        launching = value;
      },
      setStatusBarLaunchCommand: () => undefined,
      refresh: async () => undefined,
      resolveTargetFolderWithCapability: async () => folder,
      terminalForFolder: () => terminal,
      logToTerminal: (level: string, message: string) => {
        logs.push({ level, message });
      },
      executeInFreeCMTerminal: terminalSession.executeInFreeCMTerminal.bind(
        terminalSession,
      ),
      finishTerminalLogGroup: () => undefined,
    } as unknown as CommandControllerHost;
    const controller = new WorkflowController(host);

    try {
      const init = controller.runWorkflowCommand("--init");
      await flushMicrotasks();
      assert.strictEqual(launching, true);

      await controller.runWorkflowCommand("--update");
      assert.ok(
        logs.some(
          (entry) =>
            entry.level === "warning" &&
            entry.message === "Workflow launch is already in progress.",
        ),
      );

      await fs.writeFile(markerPath, "0\n", "utf8");
      await init;
      assert.strictEqual(launching, false);
    } finally {
      await fs.rm(markerDirectory, { recursive: true, force: true });
    }
  });
});
