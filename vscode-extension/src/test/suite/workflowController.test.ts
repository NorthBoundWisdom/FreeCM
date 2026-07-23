import * as assert from "assert";
import * as vscode from "vscode";

import { CommandControllerHost } from "../../controllers/commandHost";
import { WorkflowController } from "../../controllers/workflowController";
import { TerminalSessionManager } from "../../terminal/terminalSessionManager";

function createTerminal(
  execution: vscode.TerminalShellExecution,
): vscode.Terminal {
  return {
    show: () => undefined,
    shellIntegration: {
      cwd: undefined,
      executeCommand: () => execution,
    },
  } as unknown as vscode.Terminal;
}

async function flushMicrotasks(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve));
}

suite("workflow controller", () => {
  test("keeps the launch gate until the terminal command finishes", async () => {
    const folder = { name: "Host", fsPath: "/repo/Host" };
    const terminalExecution = {} as vscode.TerminalShellExecution;
    const terminal = createTerminal(terminalExecution);
    const terminalSession = new TerminalSessionManager();
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

    terminalSession.handleTerminalShellExecutionEnded({
      execution: terminalExecution,
      exitCode: 0,
    } as vscode.TerminalShellExecutionEndEvent);
    await init;
    assert.strictEqual(launching, false);
  });
});
