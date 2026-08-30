import { PullCommandTarget } from "../status/statusBar";
import { errorMessage } from "../terminal/terminalSessionManager";
import { terminalWorkflowCommand } from "../terminal/terminalWorkflowCommand";
import { displayWorkflowScriptPath } from "../workspaceDiscovery";
import { WorkflowFlag, workflowTerminalCommand } from "../workflowCommands";
import { CommandControllerHost } from "./commandHost";

export class WorkflowController {
  constructor(private readonly host: CommandControllerHost) {}

  async runWorkflowCommand(flag: WorkflowFlag): Promise<void> {
    try {
      const folder = await this.host.resolveTargetFolderWithCapability(
        (capability) => capability.hasWorkflowScript,
        "No workspace with configs/source_root_workflow.py was found.",
        "Select FreeCM workflow workspace",
        "Choose the workspace folder for this workflow command",
      );
      if (folder === undefined) {
        return;
      }
      this.host.workspaceState.invalidateCache(folder.fsPath);

      const label = `${displayWorkflowScriptPath()} ${flag}`;
      await this.host.queueInFreeCMTerminal(
        folder,
        () => this.host.terminalForFolder(folder),
        [workflowTerminalCommand(flag)],
      );
      this.host.logToTerminal("success", `Queued ${label}`, folder);
    } catch (error) {
      this.host.logToTerminal("error", errorMessage(error));
    } finally {
      this.host.finishTerminalLogGroup();
    }
  }

  async runPullCommand(target: PullCommandTarget): Promise<void> {
    try {
      const folder =
        target === "repo"
          ? await this.host.resolveWorkspaceFolderForCommand()
          : await this.host.resolveTargetFolderWithCapability(
              (capability) => capability.hasSeedRepositories,
              "No workspace with dependency seed repositories was found.",
              "Select FreeCM seed workspace",
              "Choose the workspace folder whose dependency seeds should be pulled",
            );
      if (folder === undefined) {
        return;
      }
      this.host.workspaceState.invalidateCache(folder.fsPath);
      const label = target === "seeds" ? "Pull Seeds" : "Pull";
      await this.host.queueInFreeCMTerminal(
        folder,
        () => this.host.terminalForFolder(folder),
        [
          terminalWorkflowCommand(
            target === "seeds" ? "pull-seeds" : "pull",
            folder.fsPath,
          ),
        ],
      );
      this.host.logToTerminal("success", `Queued ${label}`, folder);
    } catch (error) {
      this.host.logToTerminal("error", errorMessage(error));
    } finally {
      this.host.finishTerminalLogGroup();
    }
  }
}
