import * as path from "path";

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
  | { readonly kind: "choose"; readonly folders: readonly RepoWorkspaceFolder[] };

export function workflowScriptPath(folder: RepoWorkspaceFolder): string {
  return path.join(folder.fsPath, "configs", "source_root_workflow.py");
}

export function displayWorkflowScriptPath(): string {
  return "configs/source_root_workflow.py";
}

export async function isEligibleRepoFolder(
  folder: RepoWorkspaceFolder,
  fileSystem: FileSystemProbe,
): Promise<boolean> {
  const freeCMPath = path.join(folder.fsPath, "FreeCM");
  const activeLockPath = path.join(folder.fsPath, "source_roots.lock.jsonc");
  const templateLockPath = path.join(folder.fsPath, "source_roots.lock.jsonc.in");

  const hasFreeCM = await fileSystem.isDirectory(freeCMPath);
  if (!hasFreeCM) {
    return false;
  }

  const hasWorkflowScript = await fileSystem.exists(workflowScriptPath(folder));
  if (!hasWorkflowScript) {
    return false;
  }

  return (
    (await fileSystem.exists(activeLockPath)) ||
    (await fileSystem.exists(templateLockPath))
  );
}

export async function eligibleRepoFolders(
  folders: readonly RepoWorkspaceFolder[],
  fileSystem: FileSystemProbe,
): Promise<RepoWorkspaceFolder[]> {
  const eligible: RepoWorkspaceFolder[] = [];
  for (const folder of folders) {
    if (await isEligibleRepoFolder(folder, fileSystem)) {
      eligible.push(folder);
    }
  }
  return eligible;
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
