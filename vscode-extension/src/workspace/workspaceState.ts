import * as fs from "fs/promises";
import * as path from "path";
import * as vscode from "vscode";
import { ACTIVE_LOCK_NAME, TEMPLATE_LOCK_NAME } from "../lockSchema";
import { RepoCommandManifestState } from "../repoCommands";
import {
  LockRefreshSnapshot,
  clearManualPathStatusCache,
} from "../lockWorkflow";
import { WorkspaceCache } from "../workspaceCache";
import { beginFilesystemRead } from "../performanceMetrics";
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
  "build/dependency_seed_repos",
  ACTIVE_LOCK_NAME,
  TEMPLATE_LOCK_NAME,
  "CMakeLists.txt",
  "CMakePresets.json",
  "CMakeUserPresets.json",
  "configs/freecm.commands.jsonc",
  "configs/source_root_workflow.py",
] as const;

export interface WorkspaceCacheEntry {
  capabilities?: WorkspaceCapabilities;
  lockSnapshot?: Promise<LockRefreshSnapshot>;
  lockStatus?: { mode: string | undefined; unavailable: boolean };
  dependencyComparison?: DependencyComparisonViewState;
  dependencyComparisonExpiresAt?: number;
  repoCommandManifest?: RepoCommandManifestState | undefined;
  repoCommands?: RepoCommandViewState;
}

const nodeFileSystem: FileSystemProbe = {
  async exists(filePath: string): Promise<boolean> {
    const finishRead = beginFilesystemRead();
    try {
      await fs.access(filePath);
      return true;
    } catch {
      return false;
    } finally {
      finishRead();
    }
  },
  async isDirectory(filePath: string): Promise<boolean> {
    const finishRead = beginFilesystemRead();
    try {
      return (await fs.stat(filePath)).isDirectory();
    } catch {
      return false;
    } finally {
      finishRead();
    }
  },
};

export class FreeCMWorkspaceState {
  private readonly cache = new WorkspaceCache<WorkspaceCacheEntry>();
  private readonly workspaceFileWatchers = new Map<
    string,
    vscode.Disposable[]
  >();

  constructor(
    private readonly onWatchedFileChanged: () => void,
    private readonly fileSystem: FileSystemProbe = nodeFileSystem,
  ) {}

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
          cache.capabilities = await inspectWorkspaceCapabilities(
            folder,
            this.fileSystem,
          );
        }
        return cache.capabilities;
      }),
    );
  }

  async isDirectory(filePath: string): Promise<boolean> {
    return this.fileSystem.isDirectory(filePath);
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

  invalidateWatchedFile(folderPath: string, pattern: string): void {
    const entry = { ...(this.cache.get(folderPath) ?? {}) };
    if (pattern === ACTIVE_LOCK_NAME || pattern === TEMPLATE_LOCK_NAME) {
      delete entry.capabilities;
      delete entry.lockSnapshot;
      delete entry.lockStatus;
      delete entry.dependencyComparison;
      delete entry.dependencyComparisonExpiresAt;
      delete entry.repoCommands;
      clearManualPathStatusCache();
      this.cache.set(folderPath, entry);
      return;
    }
    if (pattern === "configs/freecm.commands.jsonc") {
      delete entry.capabilities;
      delete entry.repoCommandManifest;
      delete entry.repoCommands;
      this.cache.set(folderPath, entry);
      return;
    }
    if (
      pattern === "CMakeLists.txt" ||
      pattern === "CMakePresets.json" ||
      pattern === "CMakeUserPresets.json"
    ) {
      delete entry.repoCommands;
      this.cache.set(folderPath, entry);
      return;
    }
    if (
      pattern === "build/dependency_seed_repos" ||
      pattern === "configs/source_root_workflow.py"
    ) {
      delete entry.capabilities;
      this.cache.set(folderPath, entry);
      return;
    }
    this.invalidateCache(folderPath);
  }

  clearWorkflowViewCache(): void {
    for (const [folderPath, current] of this.cache.keyValues()) {
      const entry = { ...current };
      delete entry.lockSnapshot;
      delete entry.lockStatus;
      delete entry.dependencyComparison;
      delete entry.dependencyComparisonExpiresAt;
      this.cache.set(folderPath, entry);
    }
  }

  syncWorkspaceFileWatchers(): void {
    const workspaceFolderPaths = new Set(
      (vscode.workspace.workspaceFolders ?? []).map(
        (folder) => folder.uri.fsPath,
      ),
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

  private invalidateCacheForUri(uri: vscode.Uri, pattern: string): void {
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(uri);
    if (workspaceFolder !== undefined) {
      this.invalidateWatchedFile(workspaceFolder.uri.fsPath, pattern);
      return;
    }
    for (const folder of this.currentWorkspaceFolders()) {
      const relative = path.relative(folder.fsPath, uri.fsPath);
      if (
        relative !== "" &&
        !relative.startsWith("..") &&
        !path.isAbsolute(relative)
      ) {
        this.invalidateWatchedFile(folder.fsPath, pattern);
        return;
      }
    }
    this.clearCache();
  }

  private createWorkspaceFileWatchers(
    folder: vscode.WorkspaceFolder,
  ): vscode.Disposable[] {
    return WATCHED_WORKSPACE_FILES.map((pattern) => {
      const watcher = vscode.workspace.createFileSystemWatcher(
        new vscode.RelativePattern(folder, pattern),
      );
      const invalidate = (uri: vscode.Uri) => {
        this.invalidateCacheForUri(uri, pattern);
        this.onWatchedFileChanged();
      };
      watcher.onDidCreate(invalidate);
      watcher.onDidChange(invalidate);
      watcher.onDidDelete(invalidate);
      return watcher;
    });
  }
}

function toRepoWorkspaceFolder(
  folder: vscode.WorkspaceFolder,
): RepoWorkspaceFolder {
  return {
    name: folder.name,
    fsPath: folder.uri.fsPath,
  };
}
