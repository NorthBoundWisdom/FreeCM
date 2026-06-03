import { manualAll, pinLatest, updateUsed, usePinned } from "../lockWorkflow";
import { errorMessage } from "../terminal/terminalSessionManager";
import { LockWorkflowCommand } from "../webview/messageProtocol";
import { runOfflineUpdate } from "../workflowRunner";
import { CommandControllerHost, warnIfLaunching } from "./commandHost";

export class LockModeController {
  constructor(private readonly host: CommandControllerHost) {}

  async runLockWorkflowCommand(command: LockWorkflowCommand): Promise<void> {
    if (warnIfLaunching(this.host)) {
      return;
    }

    this.host.setLaunching(true);
    await this.host.refresh();
    let targetFolder:
      | Parameters<CommandControllerHost["terminalOutput"]>[0]
      | undefined;
    try {
      const folder = await this.host.resolveTargetFolderWithCapability(
        command === "pinLatest"
          ? (capability) =>
              capability.hasLockFile && capability.hasWorkflowScript
          : (capability) => capability.hasLockFile,
        command === "pinLatest"
          ? "Pin latest requires source_roots lock files and configs/source_root_workflow.py."
          : "No workspace with source_roots lock files was found.",
        command === "pinLatest"
          ? "Select FreeCM pin latest workspace"
          : "Select FreeCM lock workspace",
        command === "pinLatest"
          ? "Choose the workspace folder to pin latest dependencies"
          : "Choose the workspace folder for this lock command",
      );
      if (folder === undefined) {
        return;
      }
      targetFolder = folder;
      this.host.workspaceState.invalidateCache(folder.fsPath);

      if (command === "usePinned") {
        this.host.logToTerminal(
          "info",
          "Use pinned: updating active lock.",
          folder,
        );
        await usePinned(folder.fsPath, {
          output: this.host.terminalOutput(folder),
        });
        this.host.logToTerminal(
          "success",
          "Active lock now uses pinned dependencies.",
          folder,
        );
      } else if (command === "manualAll") {
        this.host.logToTerminal(
          "info",
          "Manual all: updating active lock.",
          folder,
        );
        await manualAll(folder.fsPath, {
          output: this.host.terminalOutput(folder),
        });
        this.host.logToTerminal(
          "success",
          "Active lock now uses manual seed paths.",
          folder,
        );
      } else if (command === "pinLatest") {
        this.host.logToTerminal(
          "info",
          "Pin latest: switching active lock to latest.",
          folder,
        );
        await pinLatest(
          folder.fsPath,
          (repoRoot) =>
            runOfflineUpdate(repoRoot, this.host.terminalOutput(folder)),
          { output: this.host.terminalOutput(folder) },
        );
        this.host.logToTerminal(
          "success",
          "Active lock pinned latest local seed commits.",
          folder,
        );
      } else {
        this.host.logToTerminal(
          "info",
          "Update used: syncing active lock commits to template.",
          folder,
        );
        await updateUsed(folder.fsPath);
        this.host.logToTerminal(
          "success",
          "Template lock now uses active lock dependency commits.",
          folder,
        );
      }
    } catch (error) {
      this.host.logToTerminal("error", errorMessage(error), targetFolder);
    } finally {
      if (targetFolder !== undefined) {
        this.host.workspaceState.invalidateCache(targetFolder.fsPath);
      }
      this.host.setLaunching(false);
      await this.host.refresh();
      this.host.finishTerminalLogGroup();
    }
  }
}
