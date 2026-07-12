import {
  LockRefreshSnapshot,
  MANUAL_STATUS_TTL_MS,
  readActiveLockStatus,
  readDependencyComparison,
  readLockRefreshSnapshot,
} from "../lockWorkflow";
import {
  REPO_COMMAND_ACTIONS,
  RepoCommandAction,
  RepoCommandManifestState,
  RepoCommandVariant,
  loadRepoCommandManifest,
} from "../repoCommands";
import { errorMessage } from "../terminal/terminalSessionManager";
import {
  RepoWorkspaceFolder,
  WorkspaceCapabilities,
  foldersWithCapability,
} from "../workspaceDiscovery";
import { FreeCMWorkspaceState } from "../workspace/workspaceState";
import { automaticTargetFolder } from "../workspace/workspaceDiscoveryAdapter";
import {
  CodeCountViewState,
  DependencyComparisonViewState,
  RepoCommandActionViewState,
  RepoCommandViewState,
  WorkflowViewState,
  dependencyComparisonViewState,
  emptyCodeCountViewState,
  emptyDependencyComparison,
  emptyRepoCommandActionViewStates,
  emptyRepoCommandViewState,
  repoCommandActionViewStateFromSelection,
  unavailableDependencyComparison,
} from "./workflowViewHtml";

export interface LockStatusViewState {
  readonly mode: string | undefined;
  readonly unavailable: boolean;
}

export interface WorkflowViewStateBuildInput {
  readonly workspaceFolders: readonly RepoWorkspaceFolder[];
  readonly capabilities: readonly WorkspaceCapabilities[];
  readonly activeFolder: RepoWorkspaceFolder | undefined;
  readonly workflowViewOpen: boolean;
  readonly launching: boolean;
  readonly codeCountViewState: (
    target: RepoWorkspaceFolder | undefined,
    enabled: boolean,
  ) => CodeCountViewState;
  readonly readLockStatus: (
    target: RepoWorkspaceFolder | undefined,
  ) => Promise<LockStatusViewState>;
  readonly readRepoCommandViewState: (
    target: RepoWorkspaceFolder | undefined,
  ) => Promise<RepoCommandViewState>;
  readonly readDependencyComparisonViewState: (
    target: RepoWorkspaceFolder | undefined,
  ) => Promise<DependencyComparisonViewState>;
}

export interface WorkflowViewStateBuildResult {
  readonly state: WorkflowViewState;
  readonly workspaceTarget: RepoWorkspaceFolder | undefined;
  readonly repoCommandTarget: RepoWorkspaceFolder | undefined;
  readonly repoCommands: RepoCommandViewState;
}

export class WorkflowViewStateBuilder {
  constructor(
    private readonly workspaceState: FreeCMWorkspaceState,
    private readonly selectedRepoCommandId: (
      folder: RepoWorkspaceFolder,
      action: RepoCommandAction,
    ) => string | undefined,
  ) {}

  async readLockStatus(
    target: RepoWorkspaceFolder | undefined,
  ): Promise<LockStatusViewState> {
    if (target === undefined) {
      return { mode: undefined, unavailable: false };
    }
    const cache = this.workspaceState.cacheForFolder(target);
    if (cache.lockStatus !== undefined) {
      return cache.lockStatus;
    }
    try {
      const status = await readActiveLockStatus(
        target.fsPath,
        await this.readLockSnapshot(target),
      );
      cache.lockStatus = {
        mode: status.mode,
        unavailable: status.mode === undefined,
      };
      return cache.lockStatus;
    } catch {
      cache.lockStatus = { mode: undefined, unavailable: true };
      return cache.lockStatus;
    }
  }

  async readRepoCommandViewState(
    target: RepoWorkspaceFolder | undefined,
  ): Promise<RepoCommandViewState> {
    if (target === undefined) {
      return emptyRepoCommandViewState();
    }
    const cache = this.workspaceState.cacheForFolder(target);
    if (cache.repoCommands !== undefined) {
      return cache.repoCommands;
    }
    try {
      const manifest = await this.loadRepoCommandsForFolder(target);
      if (manifest === undefined) {
        cache.repoCommands = {
          status: "missing",
          message: "No repo command manifest found",
          actions: emptyRepoCommandActionViewStates(),
        };
        return cache.repoCommands;
      }
      cache.repoCommands = {
        status: "ready",
        message: undefined,
        actions: Object.fromEntries(
          REPO_COMMAND_ACTIONS.map((action) => [
            action,
            this.repoCommandActionViewState(target, manifest, action),
          ]),
        ) as Record<RepoCommandAction, RepoCommandActionViewState>,
      };
      return cache.repoCommands;
    } catch (error) {
      cache.repoCommands = {
        status: "error",
        message: errorMessage(error),
        actions: emptyRepoCommandActionViewStates(),
      };
      return cache.repoCommands;
    }
  }

  async readDependencyComparisonViewState(
    target: RepoWorkspaceFolder | undefined,
  ): Promise<DependencyComparisonViewState> {
    if (target === undefined) {
      return emptyDependencyComparison();
    }
    const cache = this.workspaceState.cacheForFolder(target);
    if (
      cache.dependencyComparison !== undefined &&
      (cache.dependencyComparisonExpiresAt === undefined ||
        cache.dependencyComparisonExpiresAt > Date.now())
    ) {
      return cache.dependencyComparison;
    }
    delete cache.dependencyComparison;
    delete cache.dependencyComparisonExpiresAt;
    try {
      const comparison = await readDependencyComparison(
        target.fsPath,
        await this.readLockSnapshot(target),
      );
      cache.dependencyComparison = dependencyComparisonViewState(comparison);
      if (
        cache.dependencyComparison.rows.some(
          (row) => row.activeManualPathStatus !== undefined,
        )
      ) {
        cache.dependencyComparisonExpiresAt = Date.now() + MANUAL_STATUS_TTL_MS;
      }
      return cache.dependencyComparison;
    } catch {
      cache.dependencyComparison = unavailableDependencyComparison();
      return cache.dependencyComparison;
    }
  }

  private readLockSnapshot(
    target: RepoWorkspaceFolder,
  ): Promise<LockRefreshSnapshot> {
    const cache = this.workspaceState.cacheForFolder(target);
    if (cache.lockSnapshot === undefined) {
      const pending = readLockRefreshSnapshot(target.fsPath).catch((error) => {
        if (cache.lockSnapshot === pending) {
          delete cache.lockSnapshot;
        }
        throw error;
      });
      cache.lockSnapshot = pending;
    }
    return cache.lockSnapshot;
  }

  async loadRepoCommandsForFolder(
    folder: RepoWorkspaceFolder,
  ): Promise<RepoCommandManifestState | undefined> {
    const cache = this.workspaceState.cacheForFolder(folder);
    if (!("repoCommandManifest" in cache)) {
      cache.repoCommandManifest = await loadRepoCommandManifest(folder.fsPath);
    }
    return cache.repoCommandManifest;
  }

  repoCommandActionViewState(
    folder: RepoWorkspaceFolder,
    manifest: RepoCommandManifestState,
    action: RepoCommandAction,
  ): RepoCommandActionViewState {
    const variants = manifest.actions[action].variants;
    return repoCommandActionViewStateFromSelection(
      action,
      variants,
      this.selectedRepoCommandId(folder, action),
    );
  }

  explicitRepoCommandVariant(
    folder: RepoWorkspaceFolder,
    manifest: RepoCommandManifestState,
    action: RepoCommandAction,
  ): RepoCommandVariant | undefined {
    const variants = manifest.actions[action].variants;
    const selectedId = this.selectedRepoCommandId(folder, action);
    if (selectedId === undefined) {
      return undefined;
    }
    return variants.find((variant) => variant.id === selectedId);
  }

  selectedRepoCommandVariant(
    folder: RepoWorkspaceFolder,
    manifest: RepoCommandManifestState,
    action: RepoCommandAction,
  ): RepoCommandVariant | undefined {
    return (
      this.explicitRepoCommandVariant(folder, manifest, action) ??
      manifest.actions[action].defaultVariant
    );
  }
}

export async function buildWorkflowViewState(
  input: WorkflowViewStateBuildInput,
): Promise<WorkflowViewStateBuildResult> {
  const workflowFolders = foldersWithCapability(
    input.capabilities,
    (capability) => capability.hasWorkflowScript,
  );
  const lockFolders = foldersWithCapability(
    input.capabilities,
    (capability) => capability.hasLockFile,
  );
  const repoCommandFolders = foldersWithCapability(
    input.capabilities,
    (capability) => capability.hasRepoCommandManifest,
  );
  const seedFolders = foldersWithCapability(
    input.capabilities,
    (capability) => capability.hasSeedRepositories,
  );
  const pinLatestFolders = foldersWithCapability(
    input.capabilities,
    (capability) => capability.hasLockFile && capability.hasWorkflowScript,
  );
  const workspaceTarget = automaticTargetFolder(
    input.workspaceFolders,
    input.activeFolder,
  );
  const lockTarget = automaticTargetFolder(lockFolders, input.activeFolder);
  const repoCommandTarget = automaticTargetFolder(
    repoCommandFolders,
    input.activeFolder,
  );
  const [lockStatus, repoCommands, dependencyComparison] = await Promise.all([
    input.workflowViewOpen
      ? input.readLockStatus(lockTarget)
      : Promise.resolve({ mode: undefined, unavailable: false }),
    input.readRepoCommandViewState(repoCommandTarget),
    input.workflowViewOpen
      ? input.readDependencyComparisonViewState(lockTarget)
      : Promise.resolve(emptyDependencyComparison()),
  ]);

  return {
    workspaceTarget,
    repoCommandTarget,
    repoCommands,
    state: {
      workspaceCount: input.workspaceFolders.length,
      targetName: workspaceTarget?.name,
      launching: input.launching,
      commands: {
        pull: input.workspaceFolders.length > 0,
        pullSeeds: seedFolders.length > 0,
        init: workflowFolders.length > 0,
        update: workflowFolders.length > 0,
        cleanBuild: input.workspaceFolders.length > 0,
        usePinned: lockFolders.length > 0,
        pinLatest: pinLatestFolders.length > 0,
        manualAll: lockFolders.length > 0,
        updateUsed: lockFolders.length > 0,
      },
      lockMode: lockStatus.mode,
      lockStatusUnavailable: lockStatus.unavailable,
      dependencyComparison,
      repoCommands,
      codeCount: input.codeCountViewState(
        workspaceTarget,
        input.workspaceFolders.length > 0,
      ),
    },
  };
}

export function initialWorkflowViewState(): WorkflowViewState {
  return {
    workspaceCount: 0,
    targetName: undefined,
    launching: false,
    commands: emptyCommandAvailability(),
    lockMode: undefined,
    lockStatusUnavailable: false,
    dependencyComparison: unavailableDependencyComparison(),
    repoCommands: emptyRepoCommandViewState(),
    codeCount: emptyCodeCountViewState(),
  };
}

function emptyCommandAvailability(): WorkflowViewState["commands"] {
  return {
    pull: false,
    pullSeeds: false,
    init: false,
    update: false,
    cleanBuild: false,
    usePinned: false,
    pinLatest: false,
    manualAll: false,
    updateUsed: false,
  };
}
