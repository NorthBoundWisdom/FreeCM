import * as vscode from 'vscode';

import {RepoCommandAction, RepoCommandManifestState, RepoCommandVariant,} from '../repoCommands';
import {RepoCommandSelectionState} from '../repoCommandState';
import {StatusBarLaunchCommand} from '../status/statusBar';
import {TerminalLogLevel} from '../terminalLogger';
import {FreeCMWorkspaceState} from '../workspace/workspaceState';
import {RepoWorkspaceFolder, WorkspaceCapabilities,} from '../workspaceDiscovery';

export interface CommandControllerHost {
  readonly context: vscode.ExtensionContext;
  readonly workspaceState: FreeCMWorkspaceState;
  isLaunching(): boolean;
  setLaunching(value: boolean): void;
  setStatusBarLaunchCommand(command: StatusBarLaunchCommand|undefined): void;
  refresh(): Promise<void>;
  resolveWorkspaceFolderForCommand(
      title?: string,
      placeHolder?: string,
      ): Promise<RepoWorkspaceFolder|undefined>;
  resolveTargetFolderWithCapability(
      predicate: (capability: WorkspaceCapabilities) => boolean,
      missingMessage: string,
      title: string,
      placeHolder: string,
      ): Promise<RepoWorkspaceFolder|undefined>;
  terminalForFolder(folder: RepoWorkspaceFolder): Promise<vscode.Terminal>;
  terminalForRepoCommand(
      folder: RepoWorkspaceFolder,
      ): Promise<vscode.Terminal>;
  queueInFreeCMTerminal(
      folder: RepoWorkspaceFolder,
      terminalFactory: () => vscode.Terminal | Promise<vscode.Terminal>,
      lines: readonly string[],
      ): Promise<void>;
  terminalOutput(folder: RepoWorkspaceFolder):
      {log(level: TerminalLogLevel, value: string): void;};
  logToTerminal(
      level: TerminalLogLevel,
      message: string,
      folder?: RepoWorkspaceFolder,
      ): void;
  finishTerminalLogGroup(): void;
  loadRepoCommandsForFolder(
      folder: RepoWorkspaceFolder,
      ): Promise<RepoCommandManifestState|undefined>;
  selectedRepoCommandVariant(
      folder: RepoWorkspaceFolder,
      manifest: RepoCommandManifestState,
      action: RepoCommandAction,
      ): RepoCommandVariant|undefined;
  repoCommandSelectionState(
      folder: RepoWorkspaceFolder,
      ): RepoCommandSelectionState;
  updateRepoCommandSelectionState(
      folder: RepoWorkspaceFolder,
      state: RepoCommandSelectionState,
      ): Promise<void>;
  pausePanelSelectionRendering(): void;
  resumePanelSelectionRendering(): void;
}

export function warnIfLaunching(host: CommandControllerHost): boolean {
  if (!host.isLaunching()) {
    return false;
  }
  host.logToTerminal('warning', 'Workflow launch is already in progress.');
  host.finishTerminalLogGroup();
  return true;
}
