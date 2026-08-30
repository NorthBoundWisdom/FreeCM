import { errorMessage } from "../terminal/terminalSessionManager";
import {
  TerminalWorkflowAction,
  terminalWorkflowCommand,
} from "../terminal/terminalWorkflowCommand";
import {
  DependencyWorkflowCommand,
  LockWorkflowCommand,
} from "../webview/messageProtocol";
import { CommandControllerHost } from "./commandHost";

const LOCK_ACTIONS: Record<LockWorkflowCommand, TerminalWorkflowAction> = {
  usePinned: "use-pinned",
  pinLatest: "pin-latest",
  manualAll: "manual-all",
  updateUsed: "update-used",
};

const LOCK_LABELS: Record<LockWorkflowCommand, string> = {
  usePinned: "Use pinned",
  pinLatest: "Pin latest",
  manualAll: "Manual all",
  updateUsed: "Update used",
};

const DEPENDENCY_ACTIONS: Record<
  DependencyWorkflowCommand,
  TerminalWorkflowAction
> = {
  applyActiveDependencyToSample: "apply-active-dependency-to-sample",
  manualDependency: "manual-dependency",
  restoreDependencyPin: "restore-dependency-pin",
};

const DEPENDENCY_LABELS: Record<DependencyWorkflowCommand, string> = {
  applyActiveDependencyToSample: "Apply active dependency to sample",
  manualDependency: "Manual dependency",
  restoreDependencyPin: "Restore pinned dependency",
};

export class LockModeController {
  constructor(private readonly host: CommandControllerHost) {}

  async runLockWorkflowCommand(command: LockWorkflowCommand): Promise<void> {
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
      this.host.workspaceState.invalidateCache(folder.fsPath);
      await this.host.queueInFreeCMTerminal(
        folder,
        () => this.host.terminalForFolder(folder),
        [terminalWorkflowCommand(LOCK_ACTIONS[command], folder.fsPath)],
      );
      this.host.logToTerminal(
        "success",
        `Queued ${LOCK_LABELS[command]}`,
        folder,
      );
    } catch (error) {
      this.host.logToTerminal("error", errorMessage(error));
    } finally {
      this.host.finishTerminalLogGroup();
    }
  }

  async runDependencyWorkflowCommand(
    command: DependencyWorkflowCommand,
    dependency: string,
  ): Promise<void> {
    try {
      const folder = await this.host.resolveTargetFolderWithCapability(
        (capability) => capability.hasLockFile,
        "No workspace with source_roots lock files was found.",
        "Select FreeCM lock workspace",
        "Choose the workspace folder for this dependency command",
      );
      if (folder === undefined) {
        return;
      }
      this.host.workspaceState.invalidateCache(folder.fsPath);
      await this.host.queueInFreeCMTerminal(
        folder,
        () => this.host.terminalForFolder(folder),
        [
          terminalWorkflowCommand(
            DEPENDENCY_ACTIONS[command],
            folder.fsPath,
            [dependency],
          ),
        ],
      );
      this.host.logToTerminal(
        "success",
        `Queued ${DEPENDENCY_LABELS[command]}: ${dependency}`,
        folder,
      );
    } catch (error) {
      this.host.logToTerminal("error", errorMessage(error));
    } finally {
      this.host.finishTerminalLogGroup();
    }
  }
}
