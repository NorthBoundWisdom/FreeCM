import * as fs from "fs/promises";
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
  DependencyComparison,
  manualAll,
  pinLatest,
  readActiveLockStatus,
  readDependencyComparison,
  updateUsed,
  usePinned,
} from "./lockWorkflow";
import {
  FileSystemProbe,
  RepoWorkspaceFolder,
  displayWorkflowScriptPath,
  eligibleRepoFolders,
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
import { WorkspaceCache } from "./workspaceCache";
import { TerminalLogLevel, TerminalLogger } from "./terminalLogger";
import { terminalPathEnvironmentForRepo } from "./terminalPath";
import { WorkflowFlag, workflowTerminalCommand } from "./workflowCommands";
import { runOfflineUpdate } from "./workflowRunner";
import { EXTENSION_BUILD_INFO } from "./buildInfo";

const TERMINAL_NAME = "FreeCM";
const LOG_TERMINAL_NAME = "FreeCM Log";
const WORKFLOW_VIEW_ID = "freecm.workflow";
const CODE_COUNT_OUTPUT_DIR = ".freecm/counts";
const REFRESH_DEBOUNCE_MS = 75;
const PANEL_QUICK_PICK_DELAY_MS = 160;

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

function toRepoWorkspaceFolder(folder: vscode.WorkspaceFolder): RepoWorkspaceFolder {
  return {
    name: folder.name,
    fsPath: folder.uri.fsPath,
  };
}

function currentWorkspaceFolders(): RepoWorkspaceFolder[] {
  return (vscode.workspace.workspaceFolders ?? []).map(toRepoWorkspaceFolder);
}

function activeWorkspaceFolder(): RepoWorkspaceFolder | undefined {
  const activeUri = vscode.window.activeTextEditor?.document.uri;
  if (activeUri === undefined) {
    return undefined;
  }
  const folder = vscode.workspace.getWorkspaceFolder(activeUri);
  return folder === undefined ? undefined : toRepoWorkspaceFolder(folder);
}

class FreeCMExtension {
  private readonly pullStatusItem: vscode.StatusBarItem;
  private readonly repoCommandStatusItems: Record<RepoCommandAction, vscode.StatusBarItem>;
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
  private statusBarLaunchCommand: PullCommandTarget | RepoCommandAction | undefined;
  private readonly workspaceCache = new WorkspaceCache<WorkspaceCacheEntry>();
  private refreshTimer: NodeJS.Timeout | undefined;
  private refreshInFlight: Promise<void> | undefined;
  private panelSelectionDepth = 0;

  constructor(private readonly context: vscode.ExtensionContext) {
    this.pullStatusItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      100,
    );
    this.repoCommandStatusItems = {
      config: vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99),
      build: vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 98),
      run: vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 97),
      test: vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 96),
    };

    this.pullStatusItem.text = "$(repo-pull) Pull";
    this.pullStatusItem.command = "freecm.pull";
    this.repoCommandStatusItems.config.command = "freecm.config";
    this.repoCommandStatusItems.build.command = "freecm.build";
    this.repoCommandStatusItems.run.command = "freecm.run";
    this.repoCommandStatusItems.test.command = "freecm.test";

    context.subscriptions.push(
      this.pullStatusItem,
      ...REPO_COMMAND_ACTIONS.map((action) => this.repoCommandStatusItems[action]),
    );
  }

  register(): void {
    this.context.subscriptions.push(
      vscode.window.registerWebviewViewProvider(WORKFLOW_VIEW_ID, {
        resolveWebviewView: (webviewView) => {
          this.workflowView = webviewView;
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
        },
      }, {
        webviewOptions: {
          retainContextWhenHidden: true,
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
      vscode.window.onDidChangeActiveTextEditor(() => {
        this.scheduleRefresh();
      }),
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        this.clearWorkspaceCache();
        this.scheduleRefresh();
      }),
      ...this.createWorkspaceFileWatchers(),
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
    );

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
    const eligibleFolders = await this.eligibleFolders();
    const activeFolder = activeWorkspaceFolder();
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

    this.refreshStatusBar(eligibleFolders, target, repoCommands);
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

  private refreshStatusBar(
    eligibleFolders: readonly RepoWorkspaceFolder[],
    target: RepoWorkspaceFolder | undefined,
    repoCommands: RepoCommandViewState,
  ): void {
    if (eligibleFolders.length === 0) {
      this.pullStatusItem.hide();
      this.hideRepoCommandStatusItems();
      return;
    }

    const tooltipSuffix =
      target === undefined
        ? "Select an eligible workspace folder before running."
        : `${target.name}: ${target.fsPath}`;

    this.pullStatusItem.text =
      this.statusBarLaunchCommand === "repo" ? "$(sync~spin) Pull" : "$(repo-pull) Pull";
    this.pullStatusItem.tooltip =
      this.statusBarLaunchCommand === "repo"
        ? "Pulling workspace with rebase..."
        : `Run git pull --rebase\n${tooltipSuffix}`;
    this.pullStatusItem.show();
    this.refreshRepoCommandStatusBarItems(target, repoCommands);
  }

  private refreshRepoCommandStatusBarItems(
    target: RepoWorkspaceFolder | undefined,
    repoCommands: RepoCommandViewState,
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

      const icon = this.statusBarLaunchCommand === action
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

  private async eligibleFolders(): Promise<RepoWorkspaceFolder[]> {
    const folders = currentWorkspaceFolders();
    const eligibility = await Promise.all(
      folders.map(async (folder) => {
        const cache = this.cacheForFolder(folder);
        if (cache.eligible === undefined) {
          cache.eligible = await eligibleRepoFolders([folder], nodeFileSystem).then(
            (eligible) => eligible.length === 1,
          );
        }
        return { folder, eligible: cache.eligible };
      }),
    );
    return eligibility
      .filter((entry) => entry.eligible)
      .map((entry) => entry.folder);
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
      this.invalidateWorkspaceCache(folder.fsPath);

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
      if (target === "freecm" && !(await nodeFileSystem.isDirectory(repoPath))) {
        this.logToTerminal("warning", "FreeCM submodule was not found.", folder);
        return;
      }

      await pullWithRebaseIfClean(repoPath, label, this.terminalOutput(folder));
      this.invalidateWorkspaceCache(folder.fsPath);
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
        this.invalidateWorkspaceCache(folder.fsPath);
        this.logToTerminal(
          "success",
          `Selected ${titleCase(action)}: ${selected.variant.label}`,
          folder,
        );
        if (options.skipRefresh !== true) {
          await this.refresh();
        }
      } finally {
        this.panelSelectionDepth = Math.max(0, this.panelSelectionDepth - 1);
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
      this.invalidateWorkspaceCache(folder.fsPath);

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
        this.invalidateWorkspaceCache(targetFolder.fsPath);
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
    const cache = this.cacheForFolder(target);
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
    const cache = this.cacheForFolder(target);
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
    const cache = this.cacheForFolder(target);
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
    const cache = this.cacheForFolder(folder);
    if (!("repoCommandManifest" in cache)) {
      cache.repoCommandManifest = await loadRepoCommandManifest(folder.fsPath);
    }
    return cache.repoCommandManifest;
  }

  private cacheForFolder(folder: RepoWorkspaceFolder): WorkspaceCacheEntry {
    return this.workspaceCache.getOrCreate(folder.fsPath, () => ({}));
  }

  private clearWorkspaceCache(): void {
    this.workspaceCache.clear();
  }

  private invalidateWorkspaceCache(folderPath: string): void {
    this.workspaceCache.delete(folderPath);
  }

  private invalidateCacheForUri(uri: vscode.Uri): void {
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(uri);
    if (workspaceFolder !== undefined) {
      this.invalidateWorkspaceCache(workspaceFolder.uri.fsPath);
      return;
    }
    for (const folder of currentWorkspaceFolders()) {
      const relative = path.relative(folder.fsPath, uri.fsPath);
      if (relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative)) {
        this.invalidateWorkspaceCache(folder.fsPath);
        return;
      }
    }
    this.clearWorkspaceCache();
  }

  private createWorkspaceFileWatchers(): vscode.Disposable[] {
    const patterns = [
      "source_roots.lock.jsonc",
      "source_roots.lock.jsonc.in",
      "configs/freecm.commands.jsonc",
      "configs/source_root_workflow.py",
    ];
    return patterns.map((pattern) => {
      const watcher = vscode.workspace.createFileSystemWatcher(`**/${pattern}`);
      const invalidate = (uri: vscode.Uri) => {
        this.invalidateCacheForUri(uri);
        this.scheduleRefresh();
      };
      watcher.onDidCreate(invalidate);
      watcher.onDidChange(invalidate);
      watcher.onDidDelete(invalidate);
      return watcher;
    });
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
    const eligibleFolders = await this.eligibleFolders();
    const resolution = resolveTargetFolder(eligibleFolders, activeWorkspaceFolder());

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
      this.panelSelectionDepth = Math.max(0, this.panelSelectionDepth - 1);
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
    if (!(await nodeFileSystem.isDirectory(targetPath))) {
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

    const html = workflowViewHtml(this.lastViewState);
    if (html === this.lastRenderedWorkflowHtml) {
      return;
    }
    this.workflowView.webview.html = html;
    this.lastRenderedWorkflowHtml = html;
  }
}

interface WorkflowViewState {
  readonly eligibleFolders: readonly RepoWorkspaceFolder[];
  readonly targetName: string | undefined;
  readonly launching: boolean;
  readonly lockMode: string | undefined;
  readonly lockStatusUnavailable: boolean;
  readonly dependencyComparison: DependencyComparisonViewState;
  readonly repoCommands: RepoCommandViewState;
  readonly codeCount: CodeCountViewState;
}

interface CodeCountViewState {
  readonly targetPath: string | undefined;
  readonly targetLabel: string | undefined;
  readonly outputLabel: string | undefined;
}

interface DependencyComparisonViewState {
  readonly status: "ready" | "empty" | "unavailable";
  readonly sampleMode: string | undefined;
  readonly activeMode: string | undefined;
  readonly rows: readonly DependencyComparisonRowViewState[];
}

interface DependencyComparisonRowViewState {
  readonly name: string;
  readonly samplePresent: boolean;
  readonly sampleCommit: string | undefined;
  readonly activePresent: boolean;
  readonly activeCommit: string | undefined;
  readonly activeMode: string | undefined;
}

interface RepoCommandViewState {
  readonly status: "missing" | "ready" | "error";
  readonly message: string | undefined;
  readonly actions: Record<RepoCommandAction, RepoCommandActionViewState>;
}

interface RepoCommandActionViewState {
  readonly action: RepoCommandAction;
  readonly enabled: boolean;
  readonly selectedLabel: string | undefined;
  readonly variantCount: number;
}

interface WorkspaceCacheEntry {
  eligible?: boolean;
  lockStatus?: { mode: string | undefined; unavailable: boolean };
  dependencyComparison?: DependencyComparisonViewState;
  repoCommandManifest?: RepoCommandManifestState | undefined;
  repoCommands?: RepoCommandViewState;
}

type LockWorkflowCommand = "usePinned" | "pinLatest" | "manualAll" | "updateUsed";
type MaintenanceCommand = "cleanBuild" | "countCode" | "changeCountPath" | "resetCountPath";
type PullCommand = "pull" | "pullFreeCM";
type PullCommandTarget = "repo" | "freecm";
type RepoCommandSelectCommand =
  | "selectConfig"
  | "selectBuild"
  | "selectTest"
  | "selectRun";
type WorkflowCommand =
  | "init"
  | "update"
  | PullCommand
  | LockWorkflowCommand
  | MaintenanceCommand
  | RepoCommandAction
  | RepoCommandSelectCommand;

interface WorkflowMessage {
  readonly command: WorkflowCommand;
}

function isWorkflowMessage(value: unknown): value is WorkflowMessage {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const command = (value as { command?: unknown }).command;
  return (
    command === "init" ||
    command === "update" ||
    command === "pull" ||
    command === "pullFreeCM" ||
    command === "usePinned" ||
    command === "pinLatest" ||
    command === "manualAll" ||
    command === "updateUsed" ||
    command === "cleanBuild" ||
    command === "countCode" ||
    command === "changeCountPath" ||
    command === "resetCountPath" ||
    command === "config" ||
    command === "build" ||
    command === "test" ||
    command === "run" ||
    command === "selectConfig" ||
    command === "selectBuild" ||
    command === "selectTest" ||
    command === "selectRun"
  );
}

export function workflowViewHtml(state: WorkflowViewState): string {
  const hasEligibleWorkspace = state.eligibleFolders.length > 0;
  const targetLabel =
    state.targetName === undefined
      ? hasEligibleWorkspace
        ? "Multiple workspaces"
        : "No workspace"
      : escapeHtml(state.targetName);
  const targetText =
    state.targetName === undefined
      ? hasEligibleWorkspace
        ? "Multiple eligible workspaces"
        : "No eligible FreeCM workspace found"
      : "Active FreeCM workspace";
  const disabled = !hasEligibleWorkspace || state.launching ? "disabled" : "";
  const statusClass = hasEligibleWorkspace ? "ready" : "empty";
  const buildInfoText = `${escapeHtml(EXTENSION_BUILD_INFO.version)} · ${escapeHtml(
    EXTENSION_BUILD_INFO.compiledAt,
  )}`;
  const repoCommandMessage =
    state.repoCommands.status === "ready"
      ? ""
      : state.repoCommands.message === undefined
        ? ""
        : escapeHtml(state.repoCommands.message);
  const repoCommandStatusClass =
    state.repoCommands.status === "error" ? "command-status error" : "command-status";
  const dependencyComparisonHtml = dependencyComparisonSectionHtml(
    state.dependencyComparison,
  );
  const codeCountHtml = codeCountSectionHtml(state.codeCount, disabled);
  const commandRows = REPO_COMMAND_ACTIONS.map((action) =>
    repoCommandRowHtml(state.repoCommands.actions[action], disabled),
  ).join("");

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    * {
      box-sizing: border-box;
    }
    body {
      color: var(--vscode-foreground);
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      margin: 0;
      padding: 12px 10px 16px;
    }
    .panel {
      display: grid;
      gap: 12px;
    }
    .target-card {
      background: var(--vscode-sideBarSectionHeader-background);
      border: 1px solid var(--vscode-panel-border);
      border-radius: 6px;
      padding: 9px 10px;
    }
    .target-card.ready {
      border-left: 3px solid var(--vscode-testing-iconPassed);
    }
    .target-card.empty {
      border-left: 3px solid var(--vscode-testing-iconFailed);
    }
    .eyebrow {
      color: var(--vscode-descriptionForeground);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.04em;
      margin-bottom: 4px;
      text-transform: uppercase;
    }
    .build-info {
      color: var(--vscode-descriptionForeground);
      font-size: 10px;
      line-height: 1.35;
      margin-bottom: 3px;
    }
    .target-name {
      color: var(--vscode-foreground);
      font-size: 14px;
      font-weight: 600;
      line-height: 1.3;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .target-description {
      color: var(--vscode-descriptionForeground);
      line-height: 1.35;
      margin-top: 4px;
    }
    .meta-row {
      margin-top: 8px;
    }
    .pill {
      align-items: center;
      background: var(--vscode-badge-background);
      border-radius: 999px;
      color: var(--vscode-badge-foreground);
      display: inline-flex;
      font-size: 11px;
      gap: 4px;
      line-height: 1;
      min-height: 20px;
      max-width: 100%;
      padding: 4px 8px;
    }
    .mode-pill {
      background: var(--vscode-input-background);
      border: 1px solid var(--vscode-input-border, var(--vscode-panel-border));
      color: var(--vscode-descriptionForeground);
    }
    .section {
      display: grid;
      gap: 8px;
    }
    .section-header {
      align-items: center;
      display: flex;
      justify-content: space-between;
      min-height: 18px;
    }
    .section-title {
      color: var(--vscode-descriptionForeground);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .button-grid {
      display: grid;
      gap: 6px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    button {
      align-items: center;
      background: var(--vscode-button-secondaryBackground);
      border: 1px solid var(--vscode-button-border, transparent);
      border-radius: 4px;
      color: var(--vscode-button-secondaryForeground);
      cursor: pointer;
      display: flex;
      font: inherit;
      font-weight: 600;
      justify-content: center;
      min-height: 32px;
      padding: 6px 8px;
      width: 100%;
    }
    button:hover {
      background: var(--vscode-button-secondaryHoverBackground);
    }
    button:disabled {
      cursor: default;
      opacity: 0.55;
    }
    .primary {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
    }
    .primary:hover {
      background: var(--vscode-button-hoverBackground);
    }
    .command-row {
      align-items: stretch;
      display: grid;
      gap: 5px;
      grid-template-columns: minmax(0, 1fr) 30px;
      width: 100%;
    }
    .command-row .run {
      background: var(--vscode-list-hoverBackground);
      border-color: var(--vscode-panel-border);
      color: var(--vscode-foreground);
      justify-content: flex-start;
      min-width: 0;
      padding-left: 10px;
    }
    .command-row .run:hover {
      background: var(--vscode-list-activeSelectionBackground);
      color: var(--vscode-list-activeSelectionForeground);
    }
    .command-row .label {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      width: 100%;
    }
    .command-row .select {
      background: var(--vscode-input-background);
      border-color: var(--vscode-panel-border);
      color: var(--vscode-descriptionForeground);
      font-size: 11px;
      min-width: 0;
      padding: 6px 0;
      width: 30px;
    }
    .command-row .select:hover {
      background: var(--vscode-toolbar-hoverBackground);
      color: var(--vscode-foreground);
    }
    .command-status {
      color: var(--vscode-descriptionForeground);
      line-height: 1.35;
    }
    .command-status.error {
      color: var(--vscode-errorForeground);
    }
    .command-list {
      display: grid;
      gap: 6px;
    }
    .path-row {
      align-items: center;
      display: grid;
      gap: 6px;
      grid-template-columns: minmax(0, 1fr) 30px 30px 30px;
    }
    .path-value {
      align-items: center;
      background: var(--vscode-input-background);
      border: 1px solid var(--vscode-panel-border);
      border-radius: 4px;
      color: var(--vscode-foreground);
      display: flex;
      min-height: 30px;
      min-width: 0;
      overflow: hidden;
      padding: 5px 8px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .icon-button {
      align-items: center;
      background: var(--vscode-input-background);
      border-color: var(--vscode-panel-border);
      color: var(--vscode-descriptionForeground);
      font-size: 15px;
      justify-content: center;
      min-height: 30px;
      min-width: 0;
      padding: 5px 0;
      width: 30px;
    }
    .icon-button:hover {
      background: var(--vscode-toolbar-hoverBackground);
      color: var(--vscode-foreground);
    }
    .count-icon {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
    }
    .count-icon:hover {
      background: var(--vscode-button-hoverBackground);
      color: var(--vscode-button-foreground);
    }
    .count-target-label {
      color: var(--vscode-descriptionForeground);
      display: block;
      min-width: 0;
      overflow: hidden;
      text-overflow: clip;
      white-space: nowrap;
    }
    .dependency-table {
      border: 1px solid var(--vscode-panel-border);
      border-radius: 5px;
      display: grid;
      overflow: hidden;
    }
    .dependency-row {
      align-items: center;
      display: grid;
      gap: 5px;
      grid-template-columns: minmax(0, 0.92fr) 58px 58px;
      min-height: 24px;
      padding: 3px 6px;
    }
    .dependency-row + .dependency-row {
      border-top: 1px solid var(--vscode-panel-border);
    }
    .dependency-row.mismatch {
      background: color-mix(in srgb, var(--vscode-editorWarning-foreground) 11%, transparent);
      box-shadow: inset 2px 0 0 var(--vscode-editorWarning-foreground);
    }
    .dependency-head {
      background: var(--vscode-sideBarSectionHeader-background);
      color: var(--vscode-descriptionForeground);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }
    .dependency-head span:not(.dependency-name) {
      justify-self: center;
    }
    .dependency-name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .dependency-state {
      align-items: center;
      border: 1px solid var(--vscode-panel-border);
      border-radius: 999px;
      display: inline-flex;
      font-size: 10px;
      font-weight: 700;
      height: 18px;
      justify-content: center;
      justify-self: center;
      min-width: 24px;
      padding: 0 5px;
    }
    .dependency-state.pinned {
      color: var(--vscode-testing-iconPassed);
    }
    .dependency-state.latest {
      color: var(--vscode-charts-blue);
    }
    .dependency-state.manual {
      color: var(--vscode-charts-yellow);
    }
    .dependency-state.missing,
    .dependency-state.unknown {
      color: var(--vscode-descriptionForeground);
      opacity: 0.65;
    }
    .dependency-empty {
      color: var(--vscode-descriptionForeground);
      line-height: 1.35;
    }
  </style>
</head>
<body>
  <main class="panel">
    <section class="target-card ${statusClass}">
      <div class="build-info">${buildInfoText}</div>
      <div class="target-name" title="${targetLabel}">${targetLabel}</div>
    </section>

    <section class="section" aria-labelledby="workflow-title">
      <div class="section-header">
        <div id="workflow-title" class="section-title">Workflow</div>
      </div>
      <div class="button-grid">
        <button id="pull" ${disabled}>Pull</button>
        <button id="pullFreeCM" ${disabled}>Pull Submodule</button>
        <button id="init" class="primary" ${disabled}>Init</button>
        <button id="update" class="primary" ${disabled}>Update</button>
      </div>
    </section>

    ${dependencyComparisonHtml}

    <section class="section" aria-labelledby="active-lock-title">
      <div class="section-header">
        <div id="active-lock-title" class="section-title">Active Lock</div>
      </div>
      <div class="target-description">source_roots.lock.jsonc</div>
      <div class="button-grid">
        <button id="usePinned" ${disabled}>Use pinned</button>
        <button id="pinLatest" ${disabled}>Pin latest</button>
        <button id="manualAll" ${disabled}>Manual all</button>
        <button id="updateUsed" ${disabled}>Update used</button>
      </div>
    </section>

    <section class="section" aria-labelledby="maintenance-title">
      <div class="section-header">
        <div id="maintenance-title" class="section-title">Maintenance</div>
      </div>
      <button id="cleanBuild" ${disabled}>Clean build</button>
    </section>

    <section class="section" aria-labelledby="repo-commands-title">
      <div class="section-header">
        <div id="repo-commands-title" class="section-title">Project Commands</div>
      </div>
      <div class="${repoCommandStatusClass}">${repoCommandMessage}</div>
      <div class="command-list">
        ${commandRows}
      </div>
    </section>

    ${codeCountHtml}
  </main>
  <script>
    const vscode = acquireVsCodeApi();
    document.getElementById('pull').addEventListener('click', () => {
      vscode.postMessage({ command: 'pull' });
    });
    document.getElementById('pullFreeCM').addEventListener('click', () => {
      vscode.postMessage({ command: 'pullFreeCM' });
    });
    document.getElementById('init').addEventListener('click', () => {
      vscode.postMessage({ command: 'init' });
    });
    document.getElementById('update').addEventListener('click', () => {
      vscode.postMessage({ command: 'update' });
    });
    document.getElementById('usePinned').addEventListener('click', () => {
      vscode.postMessage({ command: 'usePinned' });
    });
    document.getElementById('pinLatest').addEventListener('click', () => {
      vscode.postMessage({ command: 'pinLatest' });
    });
    document.getElementById('manualAll').addEventListener('click', () => {
      vscode.postMessage({ command: 'manualAll' });
    });
    document.getElementById('updateUsed').addEventListener('click', () => {
      vscode.postMessage({ command: 'updateUsed' });
    });
    document.getElementById('cleanBuild').addEventListener('click', () => {
      vscode.postMessage({ command: 'cleanBuild' });
    });
    document.getElementById('countCode').addEventListener('click', () => {
      vscode.postMessage({ command: 'countCode' });
    });
    document.getElementById('changeCountPath').addEventListener('click', () => {
      vscode.postMessage({ command: 'changeCountPath' });
    });
    document.getElementById('resetCountPath').addEventListener('click', () => {
      vscode.postMessage({ command: 'resetCountPath' });
    });
    document.querySelectorAll('[data-command]').forEach((element) => {
      element.addEventListener('click', () => {
        vscode.postMessage({ command: element.dataset.command });
      });
    });
  </script>
</body>
</html>`;
}

function repoCommandRowHtml(
  actionState: RepoCommandActionViewState,
  globalDisabled: string,
): string {
  const disabled = globalDisabled !== "" || actionState.variantCount === 0 ? "disabled" : "";
  const selectDisabled =
    globalDisabled !== "" || actionState.variantCount === 0 ? "disabled" : "";
  const label = `${titleCase(actionState.action)}: ${
    actionState.selectedLabel === undefined
      ? "Select..."
      : escapeHtml(actionState.selectedLabel)
  }`;
  return `<div class="command-row">
    <button class="run" title="${label}" data-command="${actionState.action}" ${disabled}><span class="label">${label}</span></button>
    <button class="select" title="Select ${titleCase(
      actionState.action,
    )}" aria-label="Select ${titleCase(
      actionState.action,
    )} variant" data-command="${selectCommandForRepoAction(actionState.action)}" ${selectDisabled}>▾</button>
  </div>`;
}

function codeCountSectionHtml(
  codeCount: CodeCountViewState,
  disabled: string,
): string {
  const targetLabel = escapeHtml(codeCount.targetLabel ?? ".");
  const targetTitle = escapeHtml(codeCount.targetPath ?? "");
  return `<section class="section" aria-labelledby="code-count-title">
    <div class="section-header">
      <div id="code-count-title" class="section-title">Code Count</div>
    </div>
    <div class="path-row">
      <div class="path-value" title="${targetTitle}">${targetLabel}</div>
      <button id="resetCountPath" class="icon-button" title="Reset path" aria-label="Reset code count path" ${disabled}>↺</button>
      <button id="changeCountPath" class="icon-button" title="Change path" aria-label="Change code count path" ${disabled}>⋯</button>
      <button id="countCode" class="icon-button count-icon" title="Count code" aria-label="Count code" ${disabled}>▶</button>
    </div>
    <div class="count-target-label" title="${targetTitle}">${targetTitle}</div>
  </section>`;
}

function dependencyComparisonSectionHtml(
  comparison: DependencyComparisonViewState,
): string {
  if (comparison.status === "unavailable") {
    return `<section class="section" aria-labelledby="dependencies-title">
      <div class="section-header">
        <div id="dependencies-title" class="section-title">Dependencies</div>
      </div>
      <div class="dependency-empty">Dependency status unavailable</div>
    </section>`;
  }
  if (comparison.status === "empty") {
    return `<section class="section" aria-labelledby="dependencies-title">
      <div class="section-header">
        <div id="dependencies-title" class="section-title">Dependencies</div>
      </div>
      <div class="dependency-empty">No direct dependencies</div>
    </section>`;
  }

  const rows = comparison.rows.map((row) => {
    const name = escapeHtml(row.name);
    const mismatch = pinnedCommitsMismatch(comparison, row);
    const title = mismatch
      ? ` title="Pinned commit mismatch: sample ${escapeHtml(
          row.sampleCommit ?? "?",
        )}, active ${escapeHtml(row.activeCommit ?? "?")}"`
      : "";
    return `<div class="dependency-row${mismatch ? " mismatch" : ""}"${title}>
      <span class="dependency-name" title="${name}">${name}</span>
      ${dependencyStateHtml(comparison.sampleMode, row.samplePresent, row.sampleCommit)}
      ${dependencyStateHtml(row.activeMode, row.activePresent, row.activeCommit)}
    </div>`;
  }).join("");

  return `<section class="section" aria-labelledby="dependencies-title">
    <div class="section-header">
      <div id="dependencies-title" class="section-title">Dependencies</div>
    </div>
    <div class="dependency-table">
      <div class="dependency-row dependency-head">
        <span class="dependency-name">Dep</span>
        <span>Sample</span>
        <span>Active</span>
      </div>
      ${rows}
    </div>
  </section>`;
}

function dependencyStateHtml(
  mode: string | undefined,
  present: boolean,
  commit: string | undefined,
): string {
  if (!present) {
    return `<span class="dependency-state missing" title="Dependency not present">-</span>`;
  }
  const symbol = dependencyModeSymbol(mode);
  const cssClass = dependencyModeClass(mode);
  const label = dependencyStateLabel(mode, commit);
  const title = mode === undefined
    ? "Unavailable"
    : commit === undefined
      ? escapeHtml(mode)
      : `${escapeHtml(mode)} ${escapeHtml(commit)}`;
  return `<span class="dependency-state ${cssClass}" title="${title}">${label}</span>`;
}

function pinnedCommitsMismatch(
  comparison: DependencyComparisonViewState,
  row: DependencyComparisonRowViewState,
): boolean {
  return (
    comparison.sampleMode === "pinned" &&
    row.activeMode === "pinned" &&
    row.samplePresent &&
    row.activePresent &&
    row.sampleCommit !== undefined &&
    row.activeCommit !== undefined &&
    row.sampleCommit !== row.activeCommit
  );
}

function dependencyModeSymbol(mode: string | undefined): string {
  if (mode === "pinned") {
    return "P";
  }
  if (mode === "latest") {
    return "L";
  }
  if (mode === "manual") {
    return "M";
  }
  return "?";
}

function dependencyStateLabel(mode: string | undefined, commit: string | undefined): string {
  if (mode === "pinned") {
    return commit === undefined ? "?" : escapeHtml(shortCommit(commit));
  }
  if (mode === "manual") {
    return "manual";
  }
  return dependencyModeSymbol(mode);
}

function shortCommit(commit: string): string {
  return commit.length <= 7 ? commit : commit.slice(0, 7);
}

function dependencyModeClass(mode: string | undefined): string {
  if (mode === "pinned" || mode === "latest" || mode === "manual") {
    return mode;
  }
  return "unknown";
}

function emptyRepoCommandViewState(): RepoCommandViewState {
  return {
    status: "missing",
    message: undefined,
    actions: emptyRepoCommandActionViewStates(),
  };
}

function emptyCodeCountViewState(): CodeCountViewState {
  return {
    targetPath: undefined,
    targetLabel: undefined,
    outputLabel: undefined,
  };
}

function emptyDependencyComparison(): DependencyComparisonViewState {
  return {
    status: "empty",
    sampleMode: undefined,
    activeMode: undefined,
    rows: [],
  };
}

function unavailableDependencyComparison(): DependencyComparisonViewState {
  return {
    status: "unavailable",
    sampleMode: undefined,
    activeMode: undefined,
    rows: [],
  };
}

function dependencyComparisonViewState(
  comparison: DependencyComparison,
): DependencyComparisonViewState {
  return {
    status: comparison.rows.length === 0 ? "empty" : "ready",
    sampleMode: comparison.sampleMode,
    activeMode: comparison.activeMode,
    rows: comparison.rows.map((row) => ({
      name: row.name,
      samplePresent: row.samplePresent,
      sampleCommit: row.sampleCommit,
      activePresent: row.activePresent,
      activeCommit: row.activeCommit,
      activeMode: row.activeMode,
    })),
  };
}

function emptyRepoCommandActionViewStates(): Record<
  RepoCommandAction,
  RepoCommandActionViewState
> {
  return Object.fromEntries(
    REPO_COMMAND_ACTIONS.map((action) => [
      action,
      {
        action,
        enabled: false,
        selectedLabel: undefined,
        variantCount: 0,
      },
    ]),
  ) as Record<RepoCommandAction, RepoCommandActionViewState>;
}

export function repoCommandActionViewStateFromSelection(
  action: RepoCommandAction,
  variants: readonly RepoCommandVariant[],
  selectedId: string | undefined,
  defaultVariant?: RepoCommandVariant,
): RepoCommandActionViewState {
  const explicitSelected =
    selectedId === undefined
      ? undefined
      : variants.find((variant) => variant.id === selectedId);
  const selected = explicitSelected ?? defaultVariant;
  return {
    action,
    enabled: selected !== undefined,
    selectedLabel: selected?.label,
    variantCount: variants.length,
  };
}

function isRepoCommandAction(command: WorkflowCommand): command is RepoCommandAction {
  return command === "config" || command === "build" || command === "test" || command === "run";
}

function isRepoCommandSelectCommand(
  command: WorkflowCommand,
): command is RepoCommandSelectCommand {
  return (
    command === "selectConfig" ||
    command === "selectBuild" ||
    command === "selectTest" ||
    command === "selectRun"
  );
}

function repoCommandActionForSelectCommand(
  command: RepoCommandSelectCommand,
): RepoCommandAction {
  if (command === "selectConfig") {
    return "config";
  }
  if (command === "selectBuild") {
    return "build";
  }
  if (command === "selectTest") {
    return "test";
  }
  return "run";
}

function selectCommandForRepoAction(action: RepoCommandAction): RepoCommandSelectCommand {
  if (action === "config") {
    return "selectConfig";
  }
  if (action === "build") {
    return "selectBuild";
  }
  if (action === "test") {
    return "selectTest";
  }
  return "selectRun";
}

function statusBarIconForRepoAction(action: RepoCommandAction): string {
  if (action === "config") {
    return "$(gear)";
  }
  if (action === "build") {
    return "$(tools)";
  }
  if (action === "test") {
    return "$(beaker)";
  }
  return "$(play)";
}

type TerminalProfile =
  | { readonly kind: "default"; readonly env?: undefined; readonly signature?: undefined }
  | { readonly kind: "runtime"; readonly env: Record<string, string> | undefined; readonly signature: string };

function usesRuntimeTerminalPath(action: RepoCommandAction): boolean {
  return action === "run" || action === "test";
}

function terminalProfilesEqual(
  left: TerminalProfile | undefined,
  right: TerminalProfile,
): boolean {
  return (
    left !== undefined &&
    left.kind === right.kind &&
    left.signature === right.signature
  );
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isDisposedTerminalError(error: unknown): boolean {
  return errorMessage(error).toLowerCase().includes("terminal has already been disposed");
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

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export function sameFilePath(left: string, right: string, platform: string = process.platform): boolean {
  if (platform === "win32") {
    return path.normalize(left).toLowerCase() === path.normalize(right).toLowerCase();
  }
  return path.normalize(left) === path.normalize(right);
}

async function waitForTerminalExecutionEnd(
  execution: vscode.TerminalShellExecution,
  timeoutMs: number,
): Promise<number | undefined> {
  return await new Promise((resolve) => {
    const disposable = vscode.window.onDidEndTerminalShellExecution((event) => {
      if (event.execution !== execution) {
        return;
      }
      clearTimeout(timer);
      disposable.dispose();
      resolve(event.exitCode);
    });
    const timer = setTimeout(() => {
      disposable.dispose();
      resolve(undefined);
    }, timeoutMs);
  });
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
  isDisposedTerminalError,
};

export function deactivate(): void {
  // No global resources need explicit disposal beyond registered subscriptions.
}
