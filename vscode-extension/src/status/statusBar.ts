import * as vscode from "vscode";
import { REPO_COMMAND_ACTIONS, RepoCommandAction } from "../repoCommands";
import { RepoWorkspaceFolder } from "../workspaceDiscovery";
import { RepoCommandViewState } from "../webview/workflowViewHtml";
import {
  statusBarIconForRepoAction,
  titleCase,
} from "../commands/repoCommandActions";

export type PullCommandTarget = "repo" | "freecm";
export type StatusBarLaunchCommand = PullCommandTarget | RepoCommandAction;

export class FreeCMStatusBar {
  private readonly pullStatusItem: vscode.StatusBarItem;
  private readonly repoCommandStatusItems: Record<
    RepoCommandAction,
    vscode.StatusBarItem
  >;

  constructor(context: vscode.ExtensionContext) {
    this.pullStatusItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      100,
    );
    this.repoCommandStatusItems = {
      config: vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Left,
        99,
      ),
      build: vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Left,
        98,
      ),
      run: vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Left,
        97,
      ),
      test: vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Left,
        96,
      ),
      package: vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Left,
        95,
      ),
    };

    this.pullStatusItem.text = "$(repo-pull) Pull";
    this.pullStatusItem.command = "freecm.pull";
    this.repoCommandStatusItems.config.command = "freecm.config";
    this.repoCommandStatusItems.build.command = "freecm.build";
    this.repoCommandStatusItems.run.command = "freecm.run";
    this.repoCommandStatusItems.test.command = "freecm.test";
    this.repoCommandStatusItems.package.command = "freecm.package";

    context.subscriptions.push(
      this.pullStatusItem,
      ...REPO_COMMAND_ACTIONS.map(
        (action) => this.repoCommandStatusItems[action],
      ),
    );
  }

  refresh(
    workspaceFolders: readonly RepoWorkspaceFolder[],
    pullTarget: RepoWorkspaceFolder | undefined,
    repoCommandTarget: RepoWorkspaceFolder | undefined,
    repoCommands: RepoCommandViewState,
    launchCommand: StatusBarLaunchCommand | undefined,
  ): void {
    if (workspaceFolders.length === 0) {
      this.pullStatusItem.hide();
      this.hideRepoCommandStatusItems();
      return;
    }

    const tooltipSuffix =
      pullTarget === undefined
        ? "Select a workspace folder before running."
        : `${pullTarget.name}: ${pullTarget.fsPath}`;

    this.pullStatusItem.text =
      launchCommand === "repo" ? "$(sync~spin) Pull" : "$(repo-pull) Pull";
    this.pullStatusItem.tooltip =
      launchCommand === "repo"
        ? "Pulling workspace with rebase..."
        : `Run git pull --rebase\n${tooltipSuffix}`;
    this.pullStatusItem.show();
    this.refreshRepoCommandStatusBarItems(
      repoCommandTarget,
      repoCommands,
      launchCommand,
    );
  }

  private refreshRepoCommandStatusBarItems(
    target: RepoWorkspaceFolder | undefined,
    repoCommands: RepoCommandViewState,
    launchCommand: StatusBarLaunchCommand | undefined,
  ): void {
    if (target === undefined || repoCommands.status !== "ready") {
      this.hideRepoCommandStatusItems();
      return;
    }

    for (const action of REPO_COMMAND_ACTIONS) {
      const actionState = repoCommands.actions[action];
      const item = this.repoCommandStatusItems[action];
      if (actionState.variantCount === 0) {
        item.hide();
        continue;
      }

      const icon =
        launchCommand === action
          ? "$(sync~spin)"
          : statusBarIconForRepoAction(action);
      const selectedLabel = actionState.selectedLabel;
      item.text =
        selectedLabel === undefined
          ? `${icon} ${titleCase(action)}`
          : `${icon} ${titleCase(action)}: ${selectedLabel}`;
      item.tooltip =
        selectedLabel === undefined
          ? `Select FreeCM ${titleCase(action)} command for ${target.name}`
          : `Run FreeCM ${titleCase(action)}: ${selectedLabel}\n${target.name}: ${target.fsPath}`;
      item.show();
    }
  }

  private hideRepoCommandStatusItems(): void {
    for (const action of REPO_COMMAND_ACTIONS) {
      this.repoCommandStatusItems[action].hide();
    }
  }
}
