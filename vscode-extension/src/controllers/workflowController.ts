import {
  pullExistingSeedRepositories,
  pullWithRebaseIfClean,
} from "../gitWorkflow";
import { PullCommandTarget } from "../status/statusBar";
import { errorMessage } from "../terminal/terminalSessionManager";
import { displayWorkflowScriptPath } from "../workspaceDiscovery";
import { WorkflowFlag, workflowTerminalCommand } from "../workflowCommands";
import { CommandControllerHost, warnIfLaunching } from "./commandHost";

export class WorkflowController {
  constructor(private readonly host: CommandControllerHost) {}

  async runWorkflowCommand(flag: WorkflowFlag): Promise<void> {
    if (warnIfLaunching(this.host)) {
      return;
    }

    this.host.setLaunching(true);
    await this.host.refresh();
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
      this.host.logToTerminal("info", `Running ${label}`, folder);
      await this.host.executeInFreeCMTerminal(
        folder,
        label,
        () => this.host.terminalForFolder(folder),
        [workflowTerminalCommand(flag)],
      );
    } finally {
      this.host.setLaunching(false);
      this.host.setStatusBarLaunchCommand(undefined);
      await this.host.refresh();
      this.host.finishTerminalLogGroup();
    }
  }

  async runPullCommand(target: PullCommandTarget): Promise<void> {
    if (warnIfLaunching(this.host)) {
      return;
    }

    this.host.setLaunching(true);
    this.host.setStatusBarLaunchCommand(target);
    await this.host.refresh();
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
      if (target === "seeds") {
        await pullExistingSeedRepositories(
          folder.fsPath,
          this.host.terminalOutput(folder),
        );
      } else {
        await pullWithRebaseIfClean(
          folder.fsPath,
          folder.name,
          this.host.terminalOutput(folder),
        );
      }
      this.host.workspaceState.invalidateCache(folder.fsPath);
    } catch (error) {
      this.host.logToTerminal("error", errorMessage(error));
    } finally {
      this.host.setLaunching(false);
      this.host.setStatusBarLaunchCommand(undefined);
      await this.host.refresh();
      this.host.finishTerminalLogGroup();
    }
  }
}
