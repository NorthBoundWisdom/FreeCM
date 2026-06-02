import * as fs from "fs/promises";
import * as path from "path";
import * as vscode from "vscode";
import { RepoCommandManifestState } from "../repoCommands";
import { WorkspaceCache } from "../workspaceCache";
import {
  FileSystemProbe,
  RepoWorkspaceFolder,
  WorkspaceCapabilities,
  inspectWorkspaceCapabilities,
} from "../workspaceDiscovery";
import {
  DependencyComparisonViewState,
  RepoCommandViewState,
} from "../webview/workflowViewHtml";

export const WATCHED_WORKSPACE_FILES = [
  "FreeCM",
  "source_roots.lock.jsonc",
  "source_roots.lock.jsonc.in",
  "configs/freecm.commands.jsonc",
  "configs/source_root_workflow.py",
] as const;

export interface WorkspaceCacheEntry {
  capabilities?: WorkspaceCapabilities;
  lockStatus?: { mode: string | undefined; unavailable: boolean };
  dependencyComparison?: DependencyComparisonViewState;
  repoCommandManifest?: RepoCommandManifestState | undefined;
  repoCommands?: RepoCommandViewState;
}

const nodeFileSystem: FileSystemProbe = {
  async exists(filePath: string): Promise<boolean> {
    try {
      await fs.access(filePath);
      return true;
    } catch {
      return false;
    }
  },
  async isDirectory(filePath: string): Promise<boolean> {
    try {
      return (await fs.stat(filePath)).isDirectory();
    } catch {
      return false;
    }
  },
};

export class FreeCMWorkspaceState {
  private readonly cache = new WorkspaceCache<WorkspaceCacheEntry>();
  private readonly workspaceFileWatchers = new Map<string, vscode.Disposable[]>();

  constructor(private readonly onWatchedFileChanged: () => void) {}

  currentWorkspaceFolders(): RepoWorkspaceFolder[] {
    return (vscode.workspace.workspaceFolders ?? []).map(toRepoWorkspaceFolder);
  }

  activeWorkspaceFolder(): RepoWorkspaceFolder | undefined {
    const activeUri = vscode.window.activeTextEditor?.document.uri;
    if (activeUri === undefined) {
      return undefined;
    }
    const folder = vscode.workspace.getWorkspaceFolder(activeUri);
    return folder === undefined ? undefined : toRepoWorkspaceFolder(folder);
  }

  async workspaceCapabilities(): Promise<WorkspaceCapabilities[]> {
    const folders = this.currentWorkspaceFolders();
    return Promise.all(
      folders.map(async (folder) => {
        const cache = this.cacheForFolder(folder);
        if (cache.capabilities === undefined) {
          cache.capabilities = await inspectWorkspaceCapabilities(folder, nodeFileSystem);
        }
        return cache.capabilities;
      }),
    );
  }

  async isDirectory(filePath: string): Promise<boolean> {
    return nodeFileSystem.isDirectory(filePath);
  }

  cacheForFolder(folder: RepoWorkspaceFolder): WorkspaceCacheEntry {
    return this.cache.getOrCreate(folder.fsPath, () => ({}));
  }

  clearCache(): void {
    this.cache.clear();
  }

  invalidateCache(folderPath: string): void {
    this.cache.delete(folderPath);
  }

  clearWorkflowViewCache(): void {
    for (const entry of this.cache.values()) {
      delete entry.lockStatus;
      delete entry.dependencyComparison;
    }
  }

  syncWorkspaceFileWatchers(): void {
    const workspaceFolderPaths = new Set(
      (vscode.workspace.workspaceFolders ?? []).map((folder) => folder.uri.fsPath),
    );
    for (const [folderPath, watchers] of this.workspaceFileWatchers) {
      if (workspaceFolderPaths.has(folderPath)) {
        continue;
      }
      for (const watcher of watchers) {
        watcher.dispose();
      }
      this.workspaceFileWatchers.delete(folderPath);
    }

    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      if (this.workspaceFileWatchers.has(folder.uri.fsPath)) {
        continue;
      }
      this.workspaceFileWatchers.set(
        folder.uri.fsPath,
        this.createWorkspaceFileWatchers(folder),
      );
    }
  }

  disposeWorkspaceFileWatchers(): void {
    for (const watchers of this.workspaceFileWatchers.values()) {
      for (const watcher of watchers) {
        watcher.dispose();
      }
    }
    this.workspaceFileWatchers.clear();
  }

  private invalidateCacheForUri(uri: vscode.Uri): void {
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(uri);
    if (workspaceFolder !== undefined) {
      this.invalidateCache(workspaceFolder.uri.fsPath);
      return;
    }
    for (const folder of this.currentWorkspaceFolders()) {
      const relative = path.relative(folder.fsPath, uri.fsPath);
      if (relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative)) {
        this.invalidateCache(folder.fsPath);
        return;
      }
    }
    this.clearCache();
  }

  private createWorkspaceFileWatchers(folder: vscode.WorkspaceFolder): vscode.Disposable[] {
    return WATCHED_WORKSPACE_FILES.map((pattern) => {
      const watcher = vscode.workspace.createFileSystemWatcher(
        new vscode.RelativePattern(folder, pattern),
      );
      const invalidate = (uri: vscode.Uri) => {
        this.invalidateCacheForUri(uri);
        this.onWatchedFileChanged();
      };
      watcher.onDidCreate(invalidate);
      watcher.onDidChange(invalidate);
      watcher.onDidDelete(invalidate);
      return watcher;
    });
  }
}

function toRepoWorkspaceFolder(folder: vscode.WorkspaceFolder): RepoWorkspaceFolder {
  return {
    name: folder.name,
    fsPath: folder.uri.fsPath,
  };
}
