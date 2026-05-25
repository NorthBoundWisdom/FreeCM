import * as path from "path";
import * as vscode from "vscode";
import { cleanBuild } from "./cleanBuild";
import {
  countCode,
  isPathInside,
  normalizeCodeCountTarget,
} from "./codeCounter";
import { pullWithRebaseIfClean } from "./gitWorkflow";
import {
  manualAll,
  pinLatest,
  readActiveLockStatus,
  readDependencyComparison,
  updateUsed,
  usePinned,
} from "./lockWorkflow";
import {
  RepoWorkspaceFolder,
  displayWorkflowScriptPath,
  resolveTargetFolder,
  workflowScriptPath,
} from "./workspaceDiscovery";
import {
  REPO_COMMAND_ACTIONS,
  RepoCommandAction,
  RepoCommandManifestState,
  RepoCommandVariant,
  commandLinesForTerminal,
  loadRepoCommandManifest,
} from "./repoCommands";
import { TerminalLogLevel, TerminalLogger } from "./terminalLogger";
import {
  FreeCMStatusBar,
  PullCommandTarget,
  StatusBarLaunchCommand,
} from "./status/statusBar";
import {
  TerminalProfile,
  errorMessage,
  isDisposedTerminalError,
  terminalProfilesEqual,
  usesRuntimeTerminalPath,
  waitForTerminalExecutionEnd,
} from "./terminal/terminalRuntime";
import { terminalPathEnvironmentForRepo } from "./terminalPath";
import { WorkflowFlag, workflowTerminalCommand } from "./workflowCommands";
import { runOfflineUpdate } from "./workflowRunner";
import {
  isRepoCommandAction,
  isRepoCommandSelectCommand,
  repoCommandActionForSelectCommand,
  titleCase,
} from "./commands/repoCommandActions";
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
  workflowViewHtml,
} from "./webview/workflowViewHtml";
import {
  LockWorkflowCommand,
  MaintenanceCommand,
  PullCommand,
  WorkflowCommand,
  isWorkflowMessage,
} from "./webview/messageProtocol";
import {
  FreeCMWorkspaceState,
  WATCHED_WORKSPACE_FILES,
} from "./workspace/workspaceState";

export {
  repoCommandActionViewStateFromSelection,
  workflowViewHtml,
} from "./webview/workflowViewHtml";

const TERMINAL_NAME = "FreeCM";
const LOG_TERMINAL_NAME = "FreeCM Log";
const WORKFLOW_VIEW_ID = "freecm.workflow";
const CODE_COUNT_OUTPUT_DIR = ".freecm/counts";
const REFRESH_DEBOUNCE_MS = 75;
const PANEL_QUICK_PICK_DELAY_MS = 160;
const RETAIN_WORKFLOW_WEBVIEW_CONTEXT_WHEN_HIDDEN = false;

class FreeCMExtension {
  private readonly statusBar: FreeCMStatusBar;
  private readonly workspaceState: FreeCMWorkspaceState;
  private workflowView: vscode.WebviewView | undefined;
  private lastRenderedWorkflowHtml: string | undefined;
  private lastViewState: WorkflowViewState = {
    eligibleFolders: [],
    targetName: undefined,
    launching: false,
    lockMode: undefined,
    lockStatusUnavailable: false,
    dependencyComparison: unavailableDependencyComparison(),
    repoCommands: emptyRepoCommandViewState(),
    codeCount: emptyCodeCountViewState(),
  };
  private terminal: vscode.Terminal | undefined;
  private terminalCwd: string | undefined;
  private terminalProfile: TerminalProfile | undefined;
  private readonly terminalLogger = new TerminalLogger();
  private logTerminal: vscode.Terminal | undefined;
  private pendingRepoCommandLabel: string | undefined;
  private readonly pendingExecutions = new Map<
    vscode.TerminalShellExecution,
    { label: string; terminal: vscode.Terminal }
  >();
  private launching = false;
  private statusBarLaunchCommand: StatusBarLaunchCommand | undefined;
  private refreshTimer: NodeJS.Timeout | undefined;
  private refreshInFlight: Promise<void> | undefined;
  private panelSelectionDepth = 0;

  constructor(private readonly context: vscode.ExtensionContext) {
    this.statusBar = new FreeCMStatusBar(context);
    this.workspaceState = new FreeCMWorkspaceState(() => this.scheduleRefresh());
  }

  register(): void {
    this.context.subscriptions.push(
      vscode.window.registerWebviewViewProvider(WORKFLOW_VIEW_ID, {
        resolveWebviewView: (webviewView) => {
          this.workflowView = webviewView;
          this.lastRenderedWorkflowHtml = undefined;
          webviewView.webview.options = {
            enableScripts: true,
          };
          webviewView.webview.onDidReceiveMessage((message: unknown) => {
            if (!isWorkflowMessage(message)) {
              return;
            }
            void this.runPanelCommand(message.command);
          });
          this.renderWorkflowView();
          this.scheduleRefresh();
          webviewView.onDidDispose(() => {
            if (this.workflowView === webviewView) {
              this.workflowView = undefined;
              this.lastRenderedWorkflowHtml = undefined;
            }
          });
        },
      }, {
        webviewOptions: {
          retainContextWhenHidden: RETAIN_WORKFLOW_WEBVIEW_CONTEXT_WHEN_HIDDEN,
        },
      }),
      vscode.commands.registerCommand("freecm.init", () =>
        this.runWorkflowCommand("--init"),
      ),
      vscode.commands.registerCommand("freecm.pull", () =>
        this.runPullCommand("repo"),
      ),
      vscode.commands.registerCommand("freecm.pullFreeCM", () =>
        this.runPullCommand("freecm"),
      ),
      vscode.commands.registerCommand("freecm.update", () =>
        this.runWorkflowCommand("--update"),
      ),
      vscode.commands.registerCommand("freecm.cleanBuild", () =>
        this.runCleanBuildCommand(),
      ),
      vscode.commands.registerCommand("freecm.countCode", () =>
        this.runCodeCountCommand(),
      ),
      vscode.commands.registerCommand("freecm.config", () =>
        this.runRepoCommand("config"),
      ),
      vscode.commands.registerCommand("freecm.build", () =>
        this.runRepoCommand("build"),
      ),
      vscode.commands.registerCommand("freecm.test", () =>
        this.runRepoCommand("test"),
      ),
      vscode.commands.registerCommand("freecm.run", () =>
        this.runRepoCommand("run"),
      ),
      vscode.commands.registerCommand("freecm.package", () =>
        this.runRepoCommand("package"),
      ),
      vscode.window.onDidChangeActiveTextEditor(() => {
        this.scheduleRefresh();
      }),
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        this.workspaceState.clearCache();
        this.workspaceState.syncWorkspaceFileWatchers();
        this.scheduleRefresh();
      }),
      vscode.window.onDidCloseTerminal((closedTerminal) => {
        if (closedTerminal === this.terminal) {
          this.terminal = undefined;
          this.terminalCwd = undefined;
          this.terminalProfile = undefined;
          this.flushPendingExecutionsForTerminal(closedTerminal);
          this.flushPendingRepoCommand();
        }
        if (closedTerminal === this.logTerminal) {
          this.logTerminal = undefined;
        }
      }),
      vscode.window.onDidEndTerminalShellExecution((event) => {
        const entry = this.pendingExecutions.get(event.execution);
        if (entry === undefined) {
          return;
        }
        this.pendingExecutions.delete(event.execution);
        this.logRepoCommandFinished(entry.label, event.exitCode);
      }),
      {
        dispose: () => {
          this.workspaceState.disposeWorkspaceFileWatchers();
        },
      },
    );

    this.workspaceState.syncWorkspaceFileWatchers();
    this.scheduleRefresh();
  }

  async refresh(): Promise<void> {
    if (this.refreshInFlight !== undefined) {
      return this.refreshInFlight;
    }
    this.refreshInFlight = this.refreshNow();
    try {
      await this.refreshInFlight;
    } finally {
      this.refreshInFlight = undefined;
    }
  }

  private async refreshNow(): Promise<void> {
    const eligibleFolders = await this.workspaceState.eligibleFolders();
    const activeFolder = this.workspaceState.activeWorkspaceFolder();
    const resolution = resolveTargetFolder(eligibleFolders, activeFolder);
    const target =
      resolution.kind === "folder"
        ? resolution.folder
        : eligibleFolders.length === 1
          ? eligibleFolders[0]
          : undefined;
    const [lockStatus, repoCommands, dependencyComparison] = await Promise.all([
      this.readLockStatus(target),
      this.readRepoCommandViewState(target),
      this.readDependencyComparisonViewState(target),
    ]);

    this.lastViewState = {
      eligibleFolders,
      targetName: target?.name,
      launching: this.launching,
      lockMode: lockStatus.mode,
      lockStatusUnavailable: lockStatus.unavailable,
      dependencyComparison,
      repoCommands,
      codeCount: this.codeCountViewState(target),
    };

    this.statusBar.refresh(
      eligibleFolders,
      target,
      repoCommands,
      this.statusBarLaunchCommand,
    );
    this.renderWorkflowView();
  }

  private scheduleRefresh(): void {
    if (this.refreshTimer !== undefined) {
      clearTimeout(this.refreshTimer);
    }
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = undefined;
      void this.refresh();
    }, REFRESH_DEBOUNCE_MS);
  }

  private async runWorkflowCommand(flag: WorkflowFlag): Promise<void> {
    if (this.launching) {
      this.logToTerminal("warning", "Workflow launch is already in progress.");
      this.finishTerminalLogGroup();
      return;
    }

    this.launching = true;
    await this.refresh();
    try {
      const folder = await this.resolveTargetFolderForCommand();
      if (folder === undefined) {
        return;
      }
      this.workspaceState.invalidateCache(folder.fsPath);

      const label = `${displayWorkflowScriptPath()} ${flag}`;
      this.logToTerminal("info", `Running ${label}`, folder);
      await this.executeInFreeCMTerminal(
        folder,
        label,
        () => this.terminalForFolder(folder),
        [workflowTerminalCommand(flag)],
      );
    } finally {
      this.launching = false;
      this.statusBarLaunchCommand = undefined;
      await this.refresh();
      this.finishTerminalLogGroup();
    }
  }

  async runPanelCommand(command: WorkflowCommand): Promise<void> {
    if (command === "pull") {
      await this.runPullCommand("repo");
      return;
    }
    if (command === "pullFreeCM") {
      await this.runPullCommand("freecm");
      return;
    }
    if (command === "init") {
      await this.runWorkflowCommand("--init");
      return;
    }
    if (command === "update") {
      await this.runWorkflowCommand("--update");
      return;
    }
    if (isRepoCommandAction(command)) {
      await this.runRepoCommand(command);
      return;
    }
    if (isRepoCommandSelectCommand(command)) {
      await this.withPanelSelectionPaused(async () => {
        await delay(PANEL_QUICK_PICK_DELAY_MS);
        await this.selectRepoCommand(repoCommandActionForSelectCommand(command));
      });
      return;
    }
    if (command === "cleanBuild") {
      await this.runCleanBuildCommand();
      return;
    }
    if (command === "countCode") {
      await this.runCodeCountCommand();
      return;
    }
    if (command === "changeCountPath") {
      await this.changeCodeCountPath();
      return;
    }
    if (command === "resetCountPath") {
      await this.resetCodeCountPath();
      return;
    }
    await this.runLockWorkflowCommand(command);
  }

  private async runPullCommand(target: PullCommandTarget): Promise<void> {
    if (this.launching) {
      this.logToTerminal("warning", "Workflow launch is already in progress.");
      this.finishTerminalLogGroup();
      return;
    }

    this.launching = true;
    this.statusBarLaunchCommand = target;
    await this.refresh();
    try {
      const folder = await this.resolveTargetFolderForCommand();
      if (folder === undefined) {
        return;
      }
      const repoPath =
        target === "repo"
          ? folder.fsPath
          : path.join(folder.fsPath, "FreeCM");
      const label = target === "repo" ? folder.name : "FreeCM";
      if (target === "freecm" && !(await this.workspaceState.isDirectory(repoPath))) {
        this.logToTerminal("warning", "FreeCM submodule was not found.", folder);
        return;
      }

      await pullWithRebaseIfClean(repoPath, label, this.terminalOutput(folder));
      this.workspaceState.invalidateCache(folder.fsPath);
    } catch (error) {
      this.logToTerminal("error", errorMessage(error));
    } finally {
      this.launching = false;
      this.statusBarLaunchCommand = undefined;
      await this.refresh();
      this.finishTerminalLogGroup();
    }
  }

  private async runRepoCommand(action: RepoCommandAction): Promise<void> {
    if (this.launching) {
      this.logToTerminal("warning", "Workflow launch is already in progress.");
      this.finishTerminalLogGroup();
      return;
    }

    this.launching = true;
    this.statusBarLaunchCommand = action;
    await this.refresh();
    try {
      const folder = await this.resolveTargetFolderForCommand();
      if (folder === undefined) {
        return;
      }
      const manifest = await this.loadRepoCommandsForFolder(folder);
      if (manifest === undefined) {
        this.logToTerminal(
          "warning",
          "No configs/freecm.commands.jsonc manifest was found.",
          folder,
        );
        return;
      }
      const variant = this.selectedRepoCommandVariant(folder, manifest, action);
      if (variant === undefined) {
        this.logToTerminal(
          "warning",
          `No FreeCM ${action} command is available on this platform.`,
          folder,
        );
        return;
      }

      const label = `${titleCase(action)}: ${variant.label}`;
      this.logToTerminal("info", `Running ${label}`, folder);
      const lines = commandLinesForTerminal(variant);
      await this.executeInFreeCMTerminal(
        folder,
        label,
        () => this.terminalForRepoCommand(folder, action),
        lines,
      );
    } catch (error) {
      this.logToTerminal("error", errorMessage(error));
    } finally {
      this.launching = false;
      this.statusBarLaunchCommand = undefined;
      await this.refresh();
      this.finishTerminalLogGroup();
    }
  }

  private async selectRepoCommand(
    action: RepoCommandAction,
    options: { folder?: RepoWorkspaceFolder; skipRefresh?: boolean } = {},
  ): Promise<void> {
    const folder = options.folder ?? await this.resolveTargetFolderForCommand();
    if (folder === undefined) {
      this.finishTerminalLogGroup();
      return;
    }
    try {
      const manifest = await this.loadRepoCommandsForFolder(folder);
      if (manifest === undefined) {
        this.logToTerminal(
          "warning",
          "No configs/freecm.commands.jsonc manifest was found.",
          folder,
        );
        return;
      }
      const variants = manifest.actions[action].variants;
      if (variants.length === 0) {
        this.logToTerminal(
          "warning",
          `No FreeCM ${action} command is available on this platform.`,
          folder,
        );
        return;
      }

      const current = this.explicitRepoCommandVariant(folder, manifest, action);
      const defaultVariant = manifest.actions[action].defaultVariant;
      this.panelSelectionDepth += 1;
      try {
        const selected = await vscode.window.showQuickPick(
          variants.map((variant) => ({
            label: variant.label,
            description: variant.description,
            detail: commandLinesForTerminal(variant).join(" && "),
            picked: variant.id === (current ?? defaultVariant)?.id,
            variant,
          })),
          {
            title: `Select FreeCM ${titleCase(action)} command`,
            placeHolder: `Choose the ${action} command variant for this workspace`,
          },
        );
        if (selected === undefined) {
          return;
        }

        await this.context.workspaceState.update(
          repoCommandSelectionKey(folder, action),
          selected.variant.id,
        );
        this.workspaceState.invalidateCache(folder.fsPath);
        this.logToTerminal(
          "success",
          `Selected ${titleCase(action)}: ${selected.variant.label}`,
          folder,
        );
        if (options.skipRefresh !== true) {
          await this.refresh();
        }
      } finally {
        this.resumePanelSelectionRendering();
      }
    } catch (error) {
      this.logToTerminal("error", errorMessage(error), folder);
    } finally {
      this.finishTerminalLogGroup();
    }
  }

  private async runLockWorkflowCommand(command: LockWorkflowCommand): Promise<void> {
    if (this.launching) {
      this.logToTerminal("warning", "Workflow launch is already in progress.");
      this.finishTerminalLogGroup();
      return;
    }

    this.launching = true;
    await this.refresh();
    let targetFolder: RepoWorkspaceFolder | undefined;
    try {
      const folder = await this.resolveTargetFolderForCommand();
      if (folder === undefined) {
        return;
      }
      targetFolder = folder;
      this.workspaceState.invalidateCache(folder.fsPath);

      if (command === "usePinned") {
        this.logToTerminal("info", "Use pinned: updating active lock.", folder);
        await usePinned(folder.fsPath, { output: this.terminalOutput(folder) });
        this.logToTerminal("success", "Active lock now uses pinned dependencies.", folder);
      } else if (command === "manualAll") {
        this.logToTerminal("info", "Manual all: updating active lock.", folder);
        await manualAll(folder.fsPath, { output: this.terminalOutput(folder) });
        this.logToTerminal("success", "Active lock now uses manual seed paths.", folder);
      } else if (command === "pinLatest") {
        this.logToTerminal("info", "Pin latest: switching active lock to latest.", folder);
        await pinLatest(
          folder.fsPath,
          (repoRoot) => runOfflineUpdate(repoRoot, this.terminalOutput(folder)),
          { output: this.terminalOutput(folder) },
        );
        this.logToTerminal("success", "Active lock pinned latest local seed commits.", folder);
      } else {
        this.logToTerminal("info", "Update used: syncing active lock commits to template.", folder);
        await updateUsed(folder.fsPath);
        this.logToTerminal(
          "success",
          "Template lock now uses active lock dependency commits.",
          folder,
        );
      }
    } catch (error) {
      this.logToTerminal("error", errorMessage(error), targetFolder);
    } finally {
      if (targetFolder !== undefined) {
        this.workspaceState.invalidateCache(targetFolder.fsPath);
      }
      this.launching = false;
      await this.refresh();
      this.finishTerminalLogGroup();
    }
  }

  private async runCleanBuildCommand(): Promise<void> {
    if (this.launching) {
      this.logToTerminal("warning", "Workflow launch is already in progress.");
      this.finishTerminalLogGroup();
      return;
    }

    this.launching = true;
    await this.refresh();
    try {
      const folder = await this.resolveTargetFolderForCommand();
      if (folder === undefined) {
        return;
      }
      const confirmed = await vscode.window.showWarningMessage(
        `Clean build outputs in ${folder.name}? This only removes direct children under build/ and preserves build/dependency_seed_repos and build/dependency_source_roots.`,
        { modal: true },
        "Clean build",
      );
      if (confirmed !== "Clean build") {
        this.logToTerminal("context", `Clean build cancelled for ${folder.name}.`, folder);
        return;
      }

      this.logToTerminal(
        "warning",
        "Clean build: removing build outputs except dependency repositories.",
        folder,
      );
      const result = await cleanBuild(folder.fsPath);
      if (result.removed.length === 0) {
        this.logToTerminal(
          "success",
          `Found no build outputs to clean in ${folder.name}.`,
          folder,
        );
      } else {
        this.logToTerminal(
          "success",
          `Removed ${result.removed.length} build output item(s) in ${folder.name}.`,
          folder,
        );
      }
    } catch (error) {
      this.logToTerminal("error", errorMessage(error));
    } finally {
      this.launching = false;
      await this.refresh();
      this.finishTerminalLogGroup();
    }
  }

  private async runCodeCountCommand(): Promise<void> {
    if (this.launching) {
      this.logToTerminal("warning", "Workflow launch is already in progress.");
      this.finishTerminalLogGroup();
      return;
    }

    this.launching = true;
    await this.refresh();
    let targetFolder: RepoWorkspaceFolder | undefined;
    try {
      const folder = await this.resolveTargetFolderForCommand();
      if (folder === undefined) {
        return;
      }
      targetFolder = folder;
      const targetPath = await this.resolvedCodeCountTargetPath(folder);
      const outputRoot = path.join(folder.fsPath, CODE_COUNT_OUTPUT_DIR);
      const relativeTarget = path.relative(folder.fsPath, targetPath) || ".";
      this.logToTerminal("info", `Counting code in ${relativeTarget}`, folder);
      const report = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Window,
          title: "FreeCM code count",
        },
        async (progress) => countCode({
          workspaceRoot: folder.fsPath,
          targetPath,
          outputRoot,
          filesAssociations: vscode.workspace
            .getConfiguration("files", vscode.Uri.file(folder.fsPath))
            .get<Record<string, string>>("associations", {}),
          progress: (message) => progress.report({ message }),
        }),
      );
      this.logToTerminal(
        "success",
        `Code count wrote ${report.files.length} file result(s) to ${report.reportUri.fsPath}`,
        folder,
      );
      await vscode.commands.executeCommand("markdown.showPreview", report.reportUri);
    } catch (error) {
      this.logToTerminal("error", errorMessage(error), targetFolder);
    } finally {
      this.launching = false;
      await this.refresh();
      this.finishTerminalLogGroup();
    }
  }

  private async changeCodeCountPath(): Promise<void> {
    const folder = await this.resolveTargetFolderForCommand();
    if (folder === undefined) {
      this.finishTerminalLogGroup();
      return;
    }
    try {
      const currentTarget = await this.resolvedCodeCountTargetPath(folder);
      const selected = await vscode.window.showOpenDialog({
        title: "Select FreeCM code count folder",
        defaultUri: vscode.Uri.file(currentTarget),
        canSelectFiles: false,
        canSelectFolders: true,
        canSelectMany: false,
        openLabel: "Use Folder",
      });
      const selectedPath = selected?.[0]?.fsPath;
      if (selectedPath === undefined) {
        return;
      }
      if (!isPathInside(folder.fsPath, selectedPath)) {
        vscode.window.showWarningMessage(
          `Code count path must be inside ${folder.name}.`,
        );
        return;
      }
      await this.context.workspaceState.update(
        codeCountTargetKey(folder),
        path.resolve(selectedPath),
      );
      await this.refresh();
    } catch (error) {
      this.logToTerminal("error", errorMessage(error), folder);
    } finally {
      this.finishTerminalLogGroup();
    }
  }

  private async resetCodeCountPath(): Promise<void> {
    const folder = await this.resolveTargetFolderForCommand();
    if (folder === undefined) {
      this.finishTerminalLogGroup();
      return;
    }
    await this.context.workspaceState.update(codeCountTargetKey(folder), undefined);
    await this.refresh();
    this.finishTerminalLogGroup();
  }

  private async readLockStatus(
    target: RepoWorkspaceFolder | undefined,
  ): Promise<{ mode: string | undefined; unavailable: boolean }> {
    if (target === undefined) {
      return { mode: undefined, unavailable: false };
    }
    const cache = this.workspaceState.cacheForFolder(target);
    if (cache.lockStatus !== undefined) {
      return cache.lockStatus;
    }
    try {
      const status = await readActiveLockStatus(target.fsPath);
      cache.lockStatus = { mode: status.mode, unavailable: status.mode === undefined };
      return cache.lockStatus;
    } catch {
      cache.lockStatus = { mode: undefined, unavailable: true };
      return cache.lockStatus;
    }
  }

  private async readRepoCommandViewState(
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

  private async readDependencyComparisonViewState(
    target: RepoWorkspaceFolder | undefined,
  ): Promise<DependencyComparisonViewState> {
    if (target === undefined) {
      return emptyDependencyComparison();
    }
    const cache = this.workspaceState.cacheForFolder(target);
    if (cache.dependencyComparison !== undefined) {
      return cache.dependencyComparison;
    }
    try {
      const comparison = await readDependencyComparison(target.fsPath);
      cache.dependencyComparison = dependencyComparisonViewState(comparison);
      return cache.dependencyComparison;
    } catch {
      cache.dependencyComparison = unavailableDependencyComparison();
      return cache.dependencyComparison;
    }
  }

  private async loadRepoCommandsForFolder(
    folder: RepoWorkspaceFolder,
  ): Promise<RepoCommandManifestState | undefined> {
    const cache = this.workspaceState.cacheForFolder(folder);
    if (!("repoCommandManifest" in cache)) {
      cache.repoCommandManifest = await loadRepoCommandManifest(folder.fsPath);
    }
    return cache.repoCommandManifest;
  }

  private repoCommandActionViewState(
    folder: RepoWorkspaceFolder,
    manifest: RepoCommandManifestState,
    action: RepoCommandAction,
  ): RepoCommandActionViewState {
    const variants = manifest.actions[action].variants;
    return repoCommandActionViewStateFromSelection(
      action,
      variants,
      this.context.workspaceState.get<string>(repoCommandSelectionKey(folder, action)),
      manifest.actions[action].defaultVariant,
    );
  }

  private explicitRepoCommandVariant(
    folder: RepoWorkspaceFolder,
    manifest: RepoCommandManifestState,
    action: RepoCommandAction,
  ): RepoCommandVariant | undefined {
    const variants = manifest.actions[action].variants;
    const selectedId = this.context.workspaceState.get<string>(
      repoCommandSelectionKey(folder, action),
    );
    if (selectedId === undefined) {
      return undefined;
    }
    return variants.find((variant) => variant.id === selectedId);
  }

  private selectedRepoCommandVariant(
    folder: RepoWorkspaceFolder,
    manifest: RepoCommandManifestState,
    action: RepoCommandAction,
  ): RepoCommandVariant | undefined {
    return (
      this.explicitRepoCommandVariant(folder, manifest, action) ??
      manifest.actions[action].defaultVariant
    );
  }

  private async resolveTargetFolderForCommand(): Promise<RepoWorkspaceFolder | undefined> {
    const eligibleFolders = await this.workspaceState.eligibleFolders();
    const resolution = resolveTargetFolder(
      eligibleFolders,
      this.workspaceState.activeWorkspaceFolder(),
    );

    if (resolution.kind === "none") {
      this.logToTerminal(
        "warning",
        "No FreeCM workspace with configs/source_root_workflow.py was found.",
      );
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
        title: "Select FreeCM workspace",
        placeHolder: "Choose the workspace folder for this workflow command",
      },
    );
    return selected?.folder;
  }

  private terminalForFolder(folder: RepoWorkspaceFolder): vscode.Terminal {
    return this.terminalForFolderProfile(folder, { kind: "default" });
  }

  private async terminalForRepoCommand(
    folder: RepoWorkspaceFolder,
    action: RepoCommandAction,
  ): Promise<vscode.Terminal> {
    if (!usesRuntimeTerminalPath(action)) {
      return this.terminalForFolderProfile(folder, { kind: "default" });
    }

    const terminalPath = await terminalPathEnvironmentForRepo(folder.fsPath);
    if (terminalPath.entries.length > 0) {
      this.logToTerminal(
        "context",
        `PATH += ${terminalPath.entries.join(process.platform === "win32" ? ";" : ":")}`,
        folder,
      );
    }
    return this.terminalForFolderProfile(folder, {
      kind: "runtime",
      env: terminalPath.env,
      signature: terminalPath.entries.join("\0"),
    });
  }

  private terminalForFolderProfile(
    folder: RepoWorkspaceFolder,
    profile: TerminalProfile,
  ): vscode.Terminal {
    if (
      this.terminal !== undefined &&
      this.terminalCwd === folder.fsPath &&
      terminalProfilesEqual(this.terminalProfile, profile)
    ) {
      return this.terminal;
    }

    if (this.terminal !== undefined) {
      this.flushPendingRepoCommand();
    }
    this.terminal?.dispose();
    this.terminal = vscode.window.createTerminal({
      name: TERMINAL_NAME,
      cwd: folder.fsPath,
      env: profile.env,
    });
    this.terminalCwd = folder.fsPath;
    this.terminalProfile = profile;
    return this.terminal;
  }

  private clearTerminalReference(): void {
    const terminal = this.terminal;
    if (terminal !== undefined) {
      for (const [execution, entry] of Array.from(this.pendingExecutions)) {
        if (entry.terminal === terminal) {
          this.pendingExecutions.delete(execution);
        }
      }
    }
    this.terminal = undefined;
    this.terminalCwd = undefined;
    this.terminalProfile = undefined;
    this.pendingRepoCommandLabel = undefined;
  }

  private async executeInFreeCMTerminal(
    folder: RepoWorkspaceFolder,
    label: string,
    terminalFactory: () => vscode.Terminal | Promise<vscode.Terminal>,
    lines: readonly string[],
  ): Promise<void> {
    for (const shouldRetry of [true, false]) {
      try {
        const terminal = await terminalFactory();
        terminal.show();
        const shellIntegration = await this.waitForShellIntegration(terminal);
        if (shellIntegration !== undefined) {
          let lastExecution: vscode.TerminalShellExecution | undefined;
          await this.ensureTerminalCwd(shellIntegration, folder);
          for (const line of lines) {
            lastExecution = shellIntegration.executeCommand(line);
          }
          if (lastExecution !== undefined) {
            this.pendingExecutions.set(lastExecution, { label, terminal });
          }
        } else {
          this.pendingRepoCommandLabel = label;
          for (const line of lines) {
            terminal.sendText(line);
          }
        }
        return;
      } catch (error) {
        if (!shouldRetry || !isDisposedTerminalError(error)) {
          throw error;
        }
        this.clearTerminalReference();
        this.logToTerminal(
          "warning",
          "FreeCM terminal was already disposed; recreating it and retrying.",
          folder,
        );
      }
    }
  }

  private flushPendingRepoCommand(): void {
    if (this.pendingRepoCommandLabel === undefined) {
      return;
    }
    const label = this.pendingRepoCommandLabel;
    this.pendingRepoCommandLabel = undefined;
    this.logRepoCommandFinished(label, undefined);
  }

  private flushPendingExecutionsForTerminal(terminal: vscode.Terminal): void {
    for (const [execution, entry] of Array.from(this.pendingExecutions)) {
      if (entry.terminal === terminal) {
        this.pendingExecutions.delete(execution);
        this.logRepoCommandFinished(entry.label, undefined);
      }
    }
  }

  private logRepoCommandFinished(label: string, exitCode: number | undefined): void {
    const level: TerminalLogLevel =
      exitCode === undefined ? "info" : exitCode === 0 ? "success" : "error";
    const suffix = exitCode === undefined ? "" : ` (exit ${exitCode})`;
    this.logToTerminal(level, `Finished ${label}${suffix}`);
    this.finishTerminalLogGroup();
  }

  private async waitForShellIntegration(
    terminal: vscode.Terminal,
    timeoutMs: number = 3000,
  ): Promise<vscode.TerminalShellIntegration | undefined> {
    if (terminal.shellIntegration !== undefined) {
      return terminal.shellIntegration;
    }
    return new Promise((resolve) => {
      const disposable = vscode.window.onDidChangeTerminalShellIntegration((event) => {
        if (event.terminal !== terminal) {
          return;
        }
        clearTimeout(timer);
        disposable.dispose();
        resolve(event.shellIntegration);
      });
      const timer = setTimeout(() => {
        disposable.dispose();
        resolve(terminal.shellIntegration);
      }, timeoutMs);
    });
  }

  private async ensureTerminalCwd(
    shellIntegration: vscode.TerminalShellIntegration,
    folder: RepoWorkspaceFolder,
  ): Promise<void> {
    const currentCwd = shellIntegration.cwd;
    if (
      currentCwd === undefined ||
      currentCwd.scheme !== "file" ||
      sameFilePath(currentCwd.fsPath, folder.fsPath)
    ) {
      return;
    }

    this.logToTerminal(
      "warning",
      `FreeCM terminal was in ${currentCwd.fsPath}; switching back to ${folder.fsPath}.`,
      folder,
    );
    const execution = shellIntegration.executeCommand("cd", [folder.fsPath]);
    await waitForTerminalExecutionEnd(execution, 3000);
  }

  private terminalOutput(folder: RepoWorkspaceFolder): {
    log(level: TerminalLogLevel, value: string): void;
  } {
    return {
      log: (level, value) => {
        this.logToTerminal(level, value, folder);
      },
    };
  }

  private logToTerminal(
    level: TerminalLogLevel,
    message: string,
    folder?: RepoWorkspaceFolder,
  ): void {
    if (folder !== undefined) {
      this.terminalForFolder(folder);
    }
    if (this.logTerminal === undefined) {
      this.logTerminal = vscode.window.createTerminal({
        name: LOG_TERMINAL_NAME,
        pty: this.terminalLogger,
      });
    }
    this.logTerminal.show(true);
    this.terminalLogger.log(level, message);
  }

  private finishTerminalLogGroup(): void {
    this.terminalLogger.separator();
  }

  private async withPanelSelectionPaused<T>(operation: () => Promise<T>): Promise<T> {
    this.panelSelectionDepth += 1;
    try {
      return await operation();
    } finally {
      this.resumePanelSelectionRendering();
    }
  }

  private resumePanelSelectionRendering(): void {
    this.panelSelectionDepth = Math.max(0, this.panelSelectionDepth - 1);
    if (this.panelSelectionDepth === 0) {
      this.renderWorkflowView();
    }
  }

  private codeCountViewState(target: RepoWorkspaceFolder | undefined): CodeCountViewState {
    if (target === undefined) {
      return emptyCodeCountViewState();
    }
    const targetPath = normalizeCodeCountTarget(
      target.fsPath,
      this.context.workspaceState.get<string>(codeCountTargetKey(target)),
    );
    return {
      targetPath,
      targetLabel: path.relative(target.fsPath, targetPath) || ".",
      outputLabel: CODE_COUNT_OUTPUT_DIR,
    };
  }

  private async resolvedCodeCountTargetPath(
    folder: RepoWorkspaceFolder,
  ): Promise<string> {
    const targetPath = normalizeCodeCountTarget(
      folder.fsPath,
      this.context.workspaceState.get<string>(codeCountTargetKey(folder)),
    );
    if (!(await this.workspaceState.isDirectory(targetPath))) {
      await this.context.workspaceState.update(codeCountTargetKey(folder), undefined);
      return folder.fsPath;
    }
    return targetPath;
  }

  private renderWorkflowView(): void {
    if (this.workflowView === undefined) {
      return;
    }
    if (this.panelSelectionDepth > 0) {
      return;
    }

    const scriptUri = this.workflowView.webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "resources", "workflow.js"),
    ).toString();
    const styleUri = this.workflowView.webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "resources", "workflow.css"),
    ).toString();
    const html = workflowViewHtml(this.lastViewState, {
      cspSource: this.workflowView.webview.cspSource,
      scriptUri,
      styleUri,
    });
    if (html === this.lastRenderedWorkflowHtml) {
      return;
    }
    this.workflowView.webview.html = html;
    this.lastRenderedWorkflowHtml = html;
  }
}

function repoCommandSelectionKey(
  folder: RepoWorkspaceFolder,
  action: RepoCommandAction,
): string {
  return `repoCommands.${folder.fsPath}.${action}`;
}

function codeCountTargetKey(folder: RepoWorkspaceFolder): string {
  return `codeCount.${folder.fsPath}.targetPath`;
}

export function sameFilePath(left: string, right: string, platform: string = process.platform): boolean {
  if (platform === "win32") {
    return path.normalize(left).toLowerCase() === path.normalize(right).toLowerCase();
  }
  return path.normalize(left) === path.normalize(right);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function activate(context: vscode.ExtensionContext): void {
  const extension = new FreeCMExtension(context);
  extension.register();
}

export const __test = {
  FreeCMExtension,
  PANEL_QUICK_PICK_DELAY_MS,
  RETAIN_WORKFLOW_WEBVIEW_CONTEXT_WHEN_HIDDEN,
  WATCHED_WORKSPACE_FILES,
  isDisposedTerminalError,
};

export function deactivate(): void {
  // No global resources need explicit disposal beyond registered subscriptions.
}
