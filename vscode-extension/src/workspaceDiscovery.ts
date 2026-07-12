import * as path from "path";
import { ACTIVE_LOCK_NAME, TEMPLATE_LOCK_NAME } from "./lockSchema";

export interface RepoWorkspaceFolder {
  readonly name: string;
  readonly fsPath: string;
}

export interface FileSystemProbe {
  exists(filePath: string): Promise<boolean>;
  isDirectory(filePath: string): Promise<boolean>;
}

export type TargetResolution =
  | { readonly kind: "none" }
  | { readonly kind: "folder"; readonly folder: RepoWorkspaceFolder }
  | {
      readonly kind: "choose";
      readonly folders: readonly RepoWorkspaceFolder[];
    };

export interface WorkspaceCapabilities {
  readonly folder: RepoWorkspaceFolder;
  readonly hasSeedRepositories: boolean;
  readonly hasWorkflowScript: boolean;
  readonly hasLockFile: boolean;
  readonly hasRepoCommandManifest: boolean;
}

export function workflowScriptPath(folder: RepoWorkspaceFolder): string {
  return path.join(folder.fsPath, "configs", "source_root_workflow.py");
}

export function displayWorkflowScriptPath(): string {
  return "configs/source_root_workflow.py";
}

export function dependencySeedRootPath(folder: RepoWorkspaceFolder): string {
  return path.join(folder.fsPath, "build", "dependency_seed_repos");
}

export async function inspectWorkspaceCapabilities(
  folder: RepoWorkspaceFolder,
  fileSystem: FileSystemProbe,
): Promise<WorkspaceCapabilities> {
  const activeLockPath = path.join(folder.fsPath, ACTIVE_LOCK_NAME);
  const templateLockPath = path.join(folder.fsPath, TEMPLATE_LOCK_NAME);
  const repoCommandsPath = path.join(
    folder.fsPath,
    "configs",
    "freecm.commands.jsonc",
  );

  const [
    hasSeedRepositories,
    hasWorkflowScript,
    hasActiveLock,
    hasTemplateLock,
    hasRepoCommandManifest,
  ] = await Promise.all([
    fileSystem.isDirectory(dependencySeedRootPath(folder)),
    fileSystem.exists(workflowScriptPath(folder)),
    fileSystem.exists(activeLockPath),
    fileSystem.exists(templateLockPath),
    fileSystem.exists(repoCommandsPath),
  ]);
  return {
    folder,
    hasSeedRepositories,
    hasWorkflowScript,
    hasLockFile: hasActiveLock || hasTemplateLock,
    hasRepoCommandManifest,
  };
}

export async function workspaceCapabilities(
  folders: readonly RepoWorkspaceFolder[],
  fileSystem: FileSystemProbe,
): Promise<WorkspaceCapabilities[]> {
  return Promise.all(
    folders.map((folder) => inspectWorkspaceCapabilities(folder, fileSystem)),
  );
}

export function foldersWithCapability(
  capabilities: readonly WorkspaceCapabilities[],
  predicate: (capabilities: WorkspaceCapabilities) => boolean,
): RepoWorkspaceFolder[] {
  return capabilities
    .filter(predicate)
    .map((capabilities) => capabilities.folder);
}

export function resolveTargetFolder(
  eligibleFolders: readonly RepoWorkspaceFolder[],
  activeFolder: RepoWorkspaceFolder | undefined,
): TargetResolution {
  if (eligibleFolders.length === 0) {
    return { kind: "none" };
  }

  if (
    activeFolder !== undefined &&
    eligibleFolders.some((folder) => folder.fsPath === activeFolder.fsPath)
  ) {
    return { kind: "folder", folder: activeFolder };
  }

  if (eligibleFolders.length === 1) {
    return { kind: "folder", folder: eligibleFolders[0] };
  }

  return { kind: "choose", folders: eligibleFolders };
}
