import * as vscode from "vscode";
import {
  RepoWorkspaceFolder,
  WorkspaceCapabilities,
  foldersWithCapability,
  resolveTargetFolder,
} from "../workspaceDiscovery";
import { FreeCMWorkspaceState } from "./workspaceState";

export class WorkspaceDiscoveryAdapter {
  constructor(
    private readonly workspaceState: FreeCMWorkspaceState,
    private readonly warn: (message: string) => void,
  ) {}

  async resolveWorkspaceFolderForCommand(
    title: string = "Select workspace",
    placeHolder: string = "Choose the workspace folder for this command",
  ): Promise<RepoWorkspaceFolder | undefined> {
    const workspaceFolders = this.workspaceState.currentWorkspaceFolders();
    const resolution = resolveTargetFolder(
      workspaceFolders,
      this.workspaceState.activeWorkspaceFolder(),
    );

    if (resolution.kind === "none") {
      this.warn("No workspace folder was found.");
      return undefined;
    }

    if (resolution.kind === "folder") {
      return resolution.folder;
    }

    const selected = await vscode.window.showQuickPick(
      resolution.folders.map((folder) => ({
        label: folder.name,
        description: folder.fsPath,
        folder,
      })),
      {
        title,
        placeHolder,
      },
    );
    return selected?.folder;
  }

  async resolveTargetFolderWithCapability(
    predicate: (capability: WorkspaceCapabilities) => boolean,
    missingMessage: string,
    title: string,
    placeHolder: string,
  ): Promise<RepoWorkspaceFolder | undefined> {
    const folders = foldersWithCapability(
      await this.workspaceState.workspaceCapabilities(),
      predicate,
    );
    const resolution = resolveTargetFolder(
      folders,
      this.workspaceState.activeWorkspaceFolder(),
    );

    if (resolution.kind === "none") {
      this.warn(missingMessage);
      return undefined;
    }

    if (resolution.kind === "folder") {
      return resolution.folder;
    }

    const selected = await vscode.window.showQuickPick(
      resolution.folders.map((folder) => ({
        label: folder.name,
        description: folder.fsPath,
        folder,
      })),
      { title, placeHolder },
    );
    return selected?.folder;
  }
}

export function automaticTargetFolder(
  folders: readonly RepoWorkspaceFolder[],
  activeFolder: RepoWorkspaceFolder | undefined,
): RepoWorkspaceFolder | undefined {
  const resolution = resolveTargetFolder(folders, activeFolder);
  if (resolution.kind === "folder") {
    return resolution.folder;
  }
  return folders.length === 1 ? folders[0] : undefined;
}
