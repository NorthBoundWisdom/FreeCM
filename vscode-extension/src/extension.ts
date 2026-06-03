import * as path from "path";
import * as vscode from "vscode";
import { cleanBuild } from "./cleanBuild";
import {
  DEFAULT_CODE_COUNT_EXCLUDE_PATHS,
  countCode,
  isPathInside,
  normalizeCodeCountExcludePaths,
  normalizeCodeCountTarget,
  parseCodeCountExcludePathsText,
} from "./codeCounter";
import {
  RepoWorkspaceFolder,
  WorkspaceCapabilities,
} from "./workspaceDiscovery";
import {
  RepoCommandAction,
  RepoCommandManifestState,
  RepoCommandVariant,
} from "./repoCommands";
import { TerminalLogLevel } from "./terminalLogger";
import {
  FreeCMStatusBar,
  PullCommandTarget,
  StatusBarLaunchCommand,
} from "./status/statusBar";
import {
  TerminalSessionManager,
  errorMessage,
  isDisposedTerminalError,
} from "./terminal/terminalSessionManager";
import { WorkflowFlag } from "./workflowCommands";
import {
  isRepoCommandAction,
  isRepoCommandSelectCommand,
  repoCommandActionForSelectCommand,
} from "./commands/repoCommandActions";
import {
  CodeCountViewState,
  DependencyComparisonViewState,
  RepoCommandViewState,
  WorkflowViewState,
  repoCommandActionViewStateFromSelection,
  workflowViewHtml,
} from "./webview/workflowViewHtml";
import {
  LockStatusViewState,
  WorkflowViewStateBuilder,
  buildWorkflowViewState,
  initialWorkflowViewState,
} from "./webview/workflowViewStateBuilder";
import {
  LockWorkflowCommand,
  MaintenanceCommand,
  PullCommand,
  WorkflowCommand,
  WorkflowMessage,
  isWorkflowMessage,
} from "./webview/messageProtocol";
import {
  FreeCMWorkspaceState,
  WATCHED_WORKSPACE_FILES,
} from "./workspace/workspaceState";
import { WorkspaceDiscoveryAdapter } from "./workspace/workspaceDiscoveryAdapter";
import { CommandControllerHost } from "./controllers/commandHost";
import { LockModeController } from "./controllers/lockModeController";
import { RepoCommandController } from "./controllers/repoCommandController";
import { WorkflowController } from "./controllers/workflowController";

export {
  repoCommandActionViewStateFromSelection,
  workflowViewHtml,
} from "./webview/workflowViewHtml";
export { sameFilePath } from "./terminal/terminalSessionManager";

const WORKFLOW_VIEW_ID = "freecm.workflow";
const CODE_COUNT_OUTPUT_DIR = ".freecm/counts";
const REFRESH_DEBOUNCE_MS = 75;
const PANEL_QUICK_PICK_DELAY_MS = 160;
const RETAIN_WORKFLOW_WEBVIEW_CONTEXT_WHEN_HIDDEN = false;

class FreeCMExtension implements CommandControllerHost {
  private readonly statusBar: FreeCMStatusBar;
  readonly workspaceState: FreeCMWorkspaceState;
  private readonly workspaceDiscovery: WorkspaceDiscoveryAdapter;
  private readonly workflowViewStateBuilder: WorkflowViewStateBuilder;
  private readonly workflowController: WorkflowController;
  private readonly repoCommandController: RepoCommandController;
  private readonly lockModeController: LockModeController;
  private readonly terminalSession = new TerminalSessionManager();
  private workflowView: vscode.WebviewView | undefined;
  private lastRenderedWorkflowHtml: string | undefined;
  private lastViewState: WorkflowViewState = initialWorkflowViewState();
  private launching = false;
  private statusBarLaunchCommand: StatusBarLaunchCommand | undefined;
  private refreshTimer: NodeJS.Timeout | undefined;
  private refreshInFlight: Promise<void> | undefined;
  private panelSelectionDepth = 0;

  constructor(readonly context: vscode.ExtensionContext) {
    this.statusBar = new FreeCMStatusBar(context);
    this.workspaceState = new FreeCMWorkspaceState(() =>
      this.scheduleRefresh(),
    );
    this.workspaceDiscovery = new WorkspaceDiscoveryAdapter(
      this.workspaceState,
      (message) => this.logToTerminal("warning", message),
    );
    this.workflowViewStateBuilder = new WorkflowViewStateBuilder(
      this.workspaceState,
      (folder, action) =>
        this.context.workspaceState.get<string>(
          repoCommandSelectionKey(folder, action),
        ),
    );
    this.workflowController = new WorkflowController(this);
    this.repoCommandController = new RepoCommandController(this);
    this.lockModeController = new LockModeController(this);
  }

  register(): void {
    this.context.subscriptions.push(
      vscode.window.registerWebviewViewProvider(
        WORKFLOW_VIEW_ID,
        {
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
              void this.runPanelMessage(message);
            });
            this.renderWorkflowView();
            this.scheduleRefresh();
            webviewView.onDidDispose(() => {
              if (this.workflowView === webviewView) {
                this.workflowView = undefined;
                this.lastRenderedWorkflowHtml = undefined;
                this.workspaceState.clearWorkflowViewCache();
              }
            });
          },
        },
        {
          webviewOptions: {
            retainContextWhenHidden:
              RETAIN_WORKFLOW_WEBVIEW_CONTEXT_WHEN_HIDDEN,
          },
        },
      ),
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
        this.terminalSession.handleTerminalClosed(closedTerminal);
      }),
      vscode.window.onDidEndTerminalShellExecution((event) => {
        this.terminalSession.handleTerminalShellExecutionEnded(event);
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

  isLaunching(): boolean {
    return this.launching;
  }

  setLaunching(value: boolean): void {
    this.launching = value;
  }

  setStatusBarLaunchCommand(command: StatusBarLaunchCommand | undefined): void {
    this.statusBarLaunchCommand = command;
  }

  private async refreshNow(): Promise<void> {
    const workspaceFolders = this.workspaceState.currentWorkspaceFolders();
    const capabilities = await this.workspaceState.workspaceCapabilities();
    const activeFolder = this.workspaceState.activeWorkspaceFolder();
    const stateResult = await buildWorkflowViewState({
      workspaceFolders,
      capabilities,
      activeFolder,
      workflowViewOpen: this.workflowView !== undefined,
      launching: this.launching,
      codeCountViewState: (target, enabled) =>
        this.codeCountViewState(target, enabled),
      readLockStatus: (target) => this.readLockStatus(target),
      readRepoCommandViewState: (target) =>
        this.readRepoCommandViewState(target),
      readDependencyComparisonViewState: (target) =>
        this.readDependencyComparisonViewState(target),
    });

    this.lastViewState = stateResult.state;

    this.statusBar.refresh(
      workspaceFolders,
      stateResult.workspaceTarget,
      stateResult.repoCommandTarget,
      stateResult.repoCommands,
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
    return this.workflowController.runWorkflowCommand(flag);
  }

  async runPanelMessage(message: WorkflowMessage): Promise<void> {
    if (message.command === "saveCountExcludePaths") {
      await this.saveCodeCountExcludePaths(message.value);
      return;
    }
    await this.runPanelCommand(message.command);
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
        await this.selectRepoCommand(
          repoCommandActionForSelectCommand(command),
        );
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
    if (command === "saveCountExcludePaths") {
      return;
    }
    await this.runLockWorkflowCommand(command);
  }

  private async runPullCommand(target: PullCommandTarget): Promise<void> {
    return this.workflowController.runPullCommand(target);
  }

  private async runRepoCommand(action: RepoCommandAction): Promise<void> {
    return this.repoCommandController.runRepoCommand(action);
  }

  private async selectRepoCommand(
    action: RepoCommandAction,
    options: { folder?: RepoWorkspaceFolder; skipRefresh?: boolean } = {},
  ): Promise<void> {
    return this.repoCommandController.selectRepoCommand(action, options);
  }

  private async runLockWorkflowCommand(
    command: LockWorkflowCommand,
  ): Promise<void> {
    return this.lockModeController.runLockWorkflowCommand(command);
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
      const folder = await this.resolveWorkspaceFolderForCommand();
      if (folder === undefined) {
        return;
      }
      const confirmed = await vscode.window.showWarningMessage(
        `Clean build outputs in ${folder.name}? This only removes direct children under build/ and preserves build/dependency_seed_repos and build/dependency_source_roots.`,
        { modal: true },
        "Clean build",
      );
      if (confirmed !== "Clean build") {
        this.logToTerminal(
          "context",
          `Clean build cancelled for ${folder.name}.`,
          folder,
        );
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
      const folder = await this.resolveTargetFolderForCodeCount();
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
        async (progress) =>
          countCode({
            workspaceRoot: folder.fsPath,
            targetPath,
            outputRoot,
            filesAssociations: vscode.workspace
              .getConfiguration("files", vscode.Uri.file(folder.fsPath))
              .get<Record<string, string>>("associations", {}),
            excludePaths: this.codeCountExcludePaths(folder),
            progress: (message) => progress.report({ message }),
          }),
      );
      this.logToTerminal(
        "success",
        `Code count wrote ${report.files.length} file result(s) to ${report.reportUri.fsPath}`,
        folder,
      );
      await vscode.commands.executeCommand(
        "markdown.showPreview",
        report.reportUri,
      );
    } catch (error) {
      this.logToTerminal("error", errorMessage(error), targetFolder);
    } finally {
      this.launching = false;
      await this.refresh();
      this.finishTerminalLogGroup();
    }
  }

  private async changeCodeCountPath(): Promise<void> {
    const folder = await this.resolveTargetFolderForCodeCount();
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
    const folder = await this.resolveTargetFolderForCodeCount();
    if (folder === undefined) {
      this.finishTerminalLogGroup();
      return;
    }
    await this.context.workspaceState.update(
      codeCountTargetKey(folder),
      undefined,
    );
    await this.refresh();
    this.finishTerminalLogGroup();
  }

  private async saveCodeCountExcludePaths(value: string): Promise<void> {
    const folder = await this.resolveTargetFolderForCodeCount();
    if (folder === undefined) {
      this.finishTerminalLogGroup();
      return;
    }
    try {
      const result = parseCodeCountExcludePathsText(value);
      if (result.error !== undefined) {
        vscode.window.showWarningMessage(result.error);
        return;
      }
      await this.context.workspaceState.update(
        codeCountExcludePathsKey(folder),
        result.paths,
      );
      await this.context.workspaceState.update(
        codeCountExcludeFoldersKey(folder),
        undefined,
      );
      await this.refresh();
    } catch (error) {
      this.logToTerminal("error", errorMessage(error), folder);
    } finally {
      this.finishTerminalLogGroup();
    }
  }

  private async readLockStatus(
    target: RepoWorkspaceFolder | undefined,
  ): Promise<LockStatusViewState> {
    return this.workflowViewStateBuilder.readLockStatus(target);
  }

  private async readRepoCommandViewState(
    target: RepoWorkspaceFolder | undefined,
  ): Promise<RepoCommandViewState> {
    return this.workflowViewStateBuilder.readRepoCommandViewState(target);
  }

  private async readDependencyComparisonViewState(
    target: RepoWorkspaceFolder | undefined,
  ): Promise<DependencyComparisonViewState> {
    return this.workflowViewStateBuilder.readDependencyComparisonViewState(
      target,
    );
  }

  async loadRepoCommandsForFolder(
    folder: RepoWorkspaceFolder,
  ): Promise<RepoCommandManifestState | undefined> {
    return this.workflowViewStateBuilder.loadRepoCommandsForFolder(folder);
  }

  explicitRepoCommandVariant(
    folder: RepoWorkspaceFolder,
    manifest: RepoCommandManifestState,
    action: RepoCommandAction,
  ): RepoCommandVariant | undefined {
    return this.workflowViewStateBuilder.explicitRepoCommandVariant(
      folder,
      manifest,
      action,
    );
  }

  selectedRepoCommandVariant(
    folder: RepoWorkspaceFolder,
    manifest: RepoCommandManifestState,
    action: RepoCommandAction,
  ): RepoCommandVariant | undefined {
    return this.workflowViewStateBuilder.selectedRepoCommandVariant(
      folder,
      manifest,
      action,
    );
  }

  private async resolveTargetFolderForCodeCount(): Promise<
    RepoWorkspaceFolder | undefined
  > {
    return this.resolveWorkspaceFolderForCommand(
      "Select code count workspace",
      "Choose the workspace folder to count",
    );
  }

  async resolveWorkspaceFolderForCommand(
    title: string = "Select workspace",
    placeHolder: string = "Choose the workspace folder for this command",
  ): Promise<RepoWorkspaceFolder | undefined> {
    return this.workspaceDiscovery.resolveWorkspaceFolderForCommand(
      title,
      placeHolder,
    );
  }

  async resolveTargetFolderWithCapability(
    predicate: (capability: WorkspaceCapabilities) => boolean,
    missingMessage: string,
    title: string,
    placeHolder: string,
  ): Promise<RepoWorkspaceFolder | undefined> {
    return this.workspaceDiscovery.resolveTargetFolderWithCapability(
      predicate,
      missingMessage,
      title,
      placeHolder,
    );
  }

  terminalForFolder(folder: RepoWorkspaceFolder): vscode.Terminal {
    return this.terminalSession.terminalForFolder(folder);
  }

  async terminalForRepoCommand(
    folder: RepoWorkspaceFolder,
    action: RepoCommandAction,
  ): Promise<vscode.Terminal> {
    return this.terminalSession.terminalForRepoCommand(folder, action);
  }

  async executeInFreeCMTerminal(
    folder: RepoWorkspaceFolder,
    label: string,
    terminalFactory: () => vscode.Terminal | Promise<vscode.Terminal>,
    lines: readonly string[],
  ): Promise<void> {
    return this.terminalSession.executeInFreeCMTerminal(
      folder,
      label,
      terminalFactory,
      lines,
    );
  }

  terminalOutput(folder: RepoWorkspaceFolder): {
    log(level: TerminalLogLevel, value: string): void;
  } {
    return this.terminalSession.terminalOutput(folder);
  }

  logToTerminal(
    level: TerminalLogLevel,
    message: string,
    folder?: RepoWorkspaceFolder,
  ): void {
    this.terminalSession.logToTerminal(level, message, folder);
  }

  finishTerminalLogGroup(): void {
    this.terminalSession.finishTerminalLogGroup();
  }

  private async withPanelSelectionPaused<T>(
    operation: () => Promise<T>,
  ): Promise<T> {
    this.panelSelectionDepth += 1;
    try {
      return await operation();
    } finally {
      this.resumePanelSelectionRendering();
    }
  }

  pausePanelSelectionRendering(): void {
    this.panelSelectionDepth += 1;
  }

  resumePanelSelectionRendering(): void {
    this.panelSelectionDepth = Math.max(0, this.panelSelectionDepth - 1);
    if (this.panelSelectionDepth === 0) {
      this.renderWorkflowView();
    }
  }

  private codeCountViewState(
    target: RepoWorkspaceFolder | undefined,
    enabled: boolean,
  ): CodeCountViewState {
    if (target === undefined) {
      return {
        enabled,
        targetPath: undefined,
        targetLabel: enabled ? "Select workspace..." : undefined,
        outputLabel: enabled ? CODE_COUNT_OUTPUT_DIR : undefined,
        excludePaths: [],
      };
    }
    const targetPath = normalizeCodeCountTarget(
      target.fsPath,
      this.context.workspaceState.get<string>(codeCountTargetKey(target)),
    );
    return {
      enabled,
      targetPath,
      targetLabel: path.relative(target.fsPath, targetPath) || ".",
      outputLabel: CODE_COUNT_OUTPUT_DIR,
      excludePaths: this.codeCountExcludePaths(target),
    };
  }

  private codeCountExcludePaths(folder: RepoWorkspaceFolder): string[] {
    const stored = this.context.workspaceState.get<readonly string[]>(
      codeCountExcludePathsKey(folder),
    );
    if (stored !== undefined) {
      return normalizeCodeCountExcludePaths(stored);
    }
    const legacyFolders =
      this.context.workspaceState.get<readonly string[]>(
        codeCountExcludeFoldersKey(folder),
      ) ?? [];
    return normalizeCodeCountExcludePaths([
      ...DEFAULT_CODE_COUNT_EXCLUDE_PATHS,
      ...legacyFolders,
    ]);
  }

  private async resolvedCodeCountTargetPath(
    folder: RepoWorkspaceFolder,
  ): Promise<string> {
    const targetPath = normalizeCodeCountTarget(
      folder.fsPath,
      this.context.workspaceState.get<string>(codeCountTargetKey(folder)),
    );
    if (!(await this.workspaceState.isDirectory(targetPath))) {
      await this.context.workspaceState.update(
        codeCountTargetKey(folder),
        undefined,
      );
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

    const scriptUri = this.workflowView.webview
      .asWebviewUri(
        vscode.Uri.joinPath(
          this.context.extensionUri,
          "resources",
          "workflow.js",
        ),
      )
      .toString();
    const styleUri = this.workflowView.webview
      .asWebviewUri(
        vscode.Uri.joinPath(
          this.context.extensionUri,
          "resources",
          "workflow.css",
        ),
      )
      .toString();
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

function codeCountExcludeFoldersKey(folder: RepoWorkspaceFolder): string {
  return `codeCount.${folder.fsPath}.excludeFolders`;
}

function codeCountExcludePathsKey(folder: RepoWorkspaceFolder): string {
  return `codeCount.${folder.fsPath}.excludePaths`;
}

function emptyCommandAvailability(): WorkflowViewState["commands"] {
  return {
    pull: false,
    pullFreeCM: false,
    init: false,
    update: false,
    cleanBuild: false,
    usePinned: false,
    pinLatest: false,
    manualAll: false,
    updateUsed: false,
  };
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
  codeCountExcludeFoldersKey,
  codeCountExcludePathsKey,
  isDisposedTerminalError,
};

export function deactivate(): void {
  // No global resources need explicit disposal beyond registered subscriptions.
}
