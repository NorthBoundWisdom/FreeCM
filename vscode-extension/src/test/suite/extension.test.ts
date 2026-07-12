import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { execFileSync } from "child_process";
import {
  __test,
  repoCommandActionViewStateFromSelection,
  sameFilePath,
  workflowViewHtml,
  workflowViewRegions,
} from "../../extension";
import {
  RepoCommandAction,
  RepoCommandManifestState,
  RepoCommandVariant,
} from "../../repoCommands";
import { RepoCommandController } from "../../controllers/repoCommandController";
import { isWorkflowMessage } from "../../webview/messageProtocol";
import { FreeCMWorkspaceState } from "../../workspace/workspaceState";
import { TerminalSessionManager } from "../../terminal/terminalSessionManager";
import { captureExtensionPerformance } from "../../performanceMetrics";
import { clearManualPathStatusCache } from "../../lockWorkflow";
import { WorkflowViewStateBuilder } from "../../webview/workflowViewStateBuilder";

suite("extension", () => {
  const panelQuickPickDelayToleranceMs = 20;

  test("activates and registers workflow commands", async () => {
    const extension = vscode.extensions.getExtension("ethan-kang.freecm");
    assert.ok(extension, "extension should be discoverable");

    await extension.activate();
    const commands = await vscode.commands.getCommands(true);
    const activationEvents = extension.packageJSON.activationEvents as string[];

    assert.ok(!activationEvents.includes("onStartupFinished"));
    assert.ok(
      activationEvents.includes(
        "workspaceContains:configs/source_root_workflow.py",
      ),
    );
    assert.ok(
      activationEvents.includes(
        "workspaceContains:configs/freecm.commands.jsonc",
      ),
    );
    assert.ok(
      activationEvents.includes("workspaceContains:source_roots.lock.jsonc"),
    );
    assert.ok(
      activationEvents.includes("workspaceContains:source_roots.lock.jsonc.in"),
    );
    assert.ok(activationEvents.includes("onCommand:freecm.showWorkflowPanel"));
    assert.ok(commands.includes("freecm.showWorkflowPanel"));
    assert.ok(commands.includes("freecm.init"));
    assert.ok(commands.includes("freecm.pull"));
    assert.ok(commands.includes("freecm.pullSeeds"));
    assert.ok(!commands.includes("freecm.pullFreeCM"));
    assert.ok(activationEvents.includes("onCommand:freecm.pullSeeds"));
    assert.ok(!activationEvents.includes("onCommand:freecm.pullFreeCM"));
    assert.ok(commands.includes("freecm.update"));
    assert.ok(commands.includes("freecm.cleanBuild"));
    assert.ok(commands.includes("freecm.countCode"));
    assert.ok(commands.includes("freecm.config"));
    assert.ok(commands.includes("freecm.build"));
    assert.ok(commands.includes("freecm.test"));
    assert.ok(commands.includes("freecm.run"));
    assert.ok(commands.includes("freecm.package"));
  });

  test("contributes the workflow webview", async () => {
    const extension = vscode.extensions.getExtension("ethan-kang.freecm");
    assert.ok(extension, "extension should be discoverable");

    const packageJson = extension.packageJSON as {
      contributes?: {
        views?: Record<
          string,
          Array<{ id: string; name: string; type?: string }>
        >;
        viewsContainers?: {
          activitybar?: Array<{ id: string; title: string; icon: string }>;
        };
        commands?: Array<{ command: string; title: string }>;
      };
      extensionKind?: string[];
    };

    assert.deepStrictEqual(packageJson.extensionKind, ["workspace"]);
    assert.ok(
      packageJson.contributes?.commands?.some(
        (command) =>
          command.command === "freecm.showWorkflowPanel" &&
          command.title === "FreeCM: Show Workflow Panel",
      ),
    );
    assert.deepStrictEqual(
      packageJson.contributes?.viewsContainers?.activitybar,
      [
        {
          id: "freecm",
          title: "FreeCM",
          icon: "resources/freecm.svg",
        },
      ],
    );
    assert.deepStrictEqual(packageJson.contributes?.views?.freecm, [
      {
        id: "freecm.workflow",
        name: "Workflow",
        type: "webview",
      },
    ]);
  });

  test("workflow webview releases hidden context", () => {
    assert.strictEqual(
      __test.RETAIN_WORKFLOW_WEBVIEW_CONTEXT_WHEN_HIDDEN,
      false,
    );
  });

  test("refresh defers lock details until the workflow webview is open", async () => {
    const folder = { name: "Host", fsPath: "/repo/Host" };
    const context = {
      extensionUri: vscode.Uri.file("/repo/FreeCM/vscode-extension"),
      subscriptions: [],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    const internal = extension as unknown as {
      workflowView: vscode.WebviewView | undefined;
      lastViewState: WorkflowStateInput;
      workspaceState: {
        currentWorkspaceFolders: () => Array<typeof folder>;
        workspaceCapabilities: () => Promise<
          Array<{
            folder: typeof folder;
            hasSeedRepositories: boolean;
            hasWorkflowScript: boolean;
            hasLockFile: boolean;
            hasRepoCommandManifest: boolean;
          }>
        >;
        activeWorkspaceFolder: () => typeof folder | undefined;
      };
      readLockStatus: (
        target: typeof folder | undefined,
      ) => Promise<{ mode: "pinned"; unavailable: false }>;
      readDependencyComparisonViewState: (
        target: typeof folder | undefined,
      ) => Promise<WorkflowStateInput["dependencyComparison"]>;
    };

    internal.workspaceState.currentWorkspaceFolders = () => [folder];
    internal.workspaceState.workspaceCapabilities = async () => [
      {
        folder,
        hasSeedRepositories: true,
        hasWorkflowScript: true,
        hasLockFile: true,
        hasRepoCommandManifest: false,
      },
    ];
    internal.workspaceState.activeWorkspaceFolder = () => folder;
    let lockReads = 0;
    let comparisonReads = 0;
    internal.readLockStatus = async (target) => {
      lockReads += 1;
      assert.deepStrictEqual(target, folder);
      return { mode: "pinned", unavailable: false };
    };
    internal.readDependencyComparisonViewState = async (target) => {
      comparisonReads += 1;
      assert.deepStrictEqual(target, folder);
      return {
        status: "ready",
        sampleMode: "pinned",
        activeMode: "pinned",
        rows: [],
      };
    };

    await extension.refresh();

    assert.strictEqual(lockReads, 0);
    assert.strictEqual(comparisonReads, 0);
    assert.strictEqual(internal.lastViewState.lockMode, undefined);
    assert.strictEqual(
      internal.lastViewState.dependencyComparison.status,
      "empty",
    );

    internal.workflowView = {
      webview: {
        cspSource: "vscode-webview-resource:",
        asWebviewUri: (uri: vscode.Uri) => uri,
        html: "",
      },
    } as unknown as vscode.WebviewView;

    await extension.refresh();

    assert.strictEqual(lockReads, 1);
    assert.strictEqual(comparisonReads, 1);
    assert.strictEqual(internal.lastViewState.lockMode, "pinned");
    assert.strictEqual(
      internal.lastViewState.dependencyComparison.status,
      "ready",
    );
  });

  test("refresh runs a trailing generation for changes received in flight", async () => {
    const context = {
      extensionUri: vscode.Uri.file("/repo/FreeCM/vscode-extension"),
      subscriptions: [],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    const internal = extension as unknown as {
      refreshNow: () => Promise<void>;
    };
    let releaseFirst: (() => void) | undefined;
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let sourceGeneration = 0;
    const renderedGenerations: number[] = [];
    let runs = 0;
    internal.refreshNow = async () => {
      const captured = sourceGeneration;
      runs += 1;
      if (runs === 1) {
        await firstGate;
      }
      renderedGenerations.push(captured);
    };

    const first = extension.refresh();
    await new Promise<void>((resolve) => setImmediate(resolve));
    sourceGeneration = 1;
    const trailing = extension.refresh();
    releaseFirst?.();
    await Promise.all([first, trailing]);

    assert.deepStrictEqual(renderedGenerations, [0, 1]);
  });

  test("cold cached and watched refresh performance baselines", async () => {
    const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-refresh-perf-"));
    await Promise.all([
      fs.mkdir(path.join(repoRoot, "FreeCM")),
      fs.mkdir(path.join(repoRoot, "configs")),
    ]);
    const lock = JSON.stringify({
      schemaVersion: 5,
      depsMode: "pinned",
      dependencies: {},
      depsManualPath: {},
    });
    await Promise.all([
      fs.writeFile(path.join(repoRoot, "source_roots.lock.jsonc.in"), lock),
      fs.writeFile(path.join(repoRoot, "source_roots.lock.jsonc"), lock),
      fs.writeFile(
        path.join(repoRoot, "configs", "source_root_workflow.py"),
        "# workflow fixture\n",
      ),
      fs.writeFile(
        path.join(repoRoot, "configs", "freecm.commands.jsonc"),
        JSON.stringify({ version: 1, commands: {} }),
      ),
    ]);
    const folder = { name: "Host", fsPath: repoRoot };
    const activationContext = {
      extensionUri: vscode.Uri.file("/repo/FreeCM/vscode-extension"),
      subscriptions: [] as vscode.Disposable[],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const disposable = (): vscode.Disposable => ({ dispose: () => undefined });
    const registration = {
      registerWebviewViewProvider: disposable,
      registerCommand: disposable,
      onDidChangeActiveTextEditor: disposable,
      onDidChangeWorkspaceFolders: disposable,
      onDidCloseTerminal: disposable,
      onDidEndTerminalShellExecution: disposable,
    };
    const activation = await captureExtensionPerformance(
      "cold-activation",
      async () => {
        const extension = new __test.FreeCMExtension(activationContext);
        extension.workspaceState.currentWorkspaceFolders = () => [folder];
        extension.workspaceState.activeWorkspaceFolder = () => folder;
        extension.register(registration);
        await new Promise<void>((resolve) => setTimeout(resolve, 125));
        return extension;
      },
    );
    for (const subscription of activationContext.subscriptions) {
      subscription.dispose();
    }

    const context = {
      extensionUri: vscode.Uri.file("/repo/FreeCM/vscode-extension"),
      subscriptions: [] as vscode.Disposable[],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    const internal = extension as unknown as {
      workflowView: vscode.WebviewView;
      workspaceState: FreeCMWorkspaceState & {
        currentWorkspaceFolders: () => Array<typeof folder>;
        activeWorkspaceFolder: () => typeof folder;
      };
    };
    internal.workspaceState.currentWorkspaceFolders = () => [folder];
    internal.workspaceState.activeWorkspaceFolder = () => folder;
    internal.workflowView = {
      webview: {
        cspSource: "vscode-webview-resource:",
        asWebviewUri: (uri: vscode.Uri) => uri,
        html: "",
        postMessage: async () => true,
      },
    } as unknown as vscode.WebviewView;

    const cold = await captureExtensionPerformance("cold-refresh", () =>
      extension.refresh(),
    );
    const cached = await captureExtensionPerformance("cached-refresh", () =>
      extension.refresh(),
    );
    internal.workspaceState.invalidateWatchedFile(
      repoRoot,
      "source_roots.lock.jsonc",
    );
    const watched = await captureExtensionPerformance("watched-file-refresh", () =>
      extension.refresh(),
    );

    assert.strictEqual(activation.report.filesystemReads, 6);
    assert.strictEqual(cold.report.filesystemReads, 9);
    assert.strictEqual(cached.report.filesystemReads, 0);
    assert.strictEqual(watched.report.filesystemReads, 8);
    for (const report of [
      activation.report,
      cold.report,
      cached.report,
      watched.report,
    ]) {
      assert.ok(report.durationMs < 5_000);
      assert.ok(report.peakConcurrentReads <= 5);
    }
    for (const subscription of context.subscriptions) {
      subscription.dispose();
    }
  });

  test("workflow refresh patches changed regions without replacing HTML", () => {
    const context = {
      extensionUri: vscode.Uri.file("/repo/FreeCM/vscode-extension"),
      subscriptions: [],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    const posted: unknown[] = [];
    let html = "";
    let htmlWrites = 0;
    const webview = {
      cspSource: "vscode-webview-resource:",
      asWebviewUri: (uri: vscode.Uri) => uri,
      postMessage: async (message: unknown) => {
        posted.push(message);
        return true;
      },
      get html() {
        return html;
      },
      set html(value: string) {
        html = value;
        htmlWrites += 1;
      },
    };
    const internal = extension as unknown as {
      workflowView: vscode.WebviewView;
      lastViewState: WorkflowStateInput;
      renderWorkflowView: () => void;
    };
    internal.workflowView = { webview } as unknown as vscode.WebviewView;
    internal.lastViewState = testWorkflowState({
      workspaceCount: 1,
      commands: availableCommands(),
    });

    internal.renderWorkflowView();
    const initialHtml = html;
    internal.lastViewState = testWorkflowState({
      workspaceCount: 1,
      launching: true,
      commands: availableCommands(),
    });
    internal.renderWorkflowView();

    assert.strictEqual(htmlWrites, 1);
    assert.strictEqual(html, initialHtml);
    assert.strictEqual(posted.length, 1);
    const update = posted[0] as {
      type: string;
      version: number;
      regions: Record<string, string>;
    };
    assert.strictEqual(update.type, "workflowState");
    assert.strictEqual(update.version, 1);
    assert.ok(Object.keys(update.regions).length > 0);
  });

  test("workflow view rebuilds after failed or hidden delivery", async () => {
    const context = {
      extensionUri: vscode.Uri.file("/repo/FreeCM/vscode-extension"),
      subscriptions: [],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    let visible = true;
    let delivered = true;
    let html = "";
    let htmlWrites = 0;
    const view = {
      get visible() {
        return visible;
      },
      webview: {
        cspSource: "vscode-webview-resource:",
        asWebviewUri: (uri: vscode.Uri) => uri,
        postMessage: async () => delivered,
        get html() {
          return html;
        },
        set html(value: string) {
          html = value;
          htmlWrites += 1;
        },
      },
    } as unknown as vscode.WebviewView;
    const internal = extension as unknown as {
      workflowView: vscode.WebviewView;
      lastViewState: WorkflowStateInput;
      renderWorkflowView: () => void;
    };
    internal.workflowView = view;
    internal.lastViewState = testWorkflowState({
      workspaceCount: 1,
      targetName: "Initial",
      commands: availableCommands(),
    });
    internal.renderWorkflowView();

    delivered = false;
    internal.lastViewState = testWorkflowState({
      workspaceCount: 1,
      targetName: "Failed delivery",
      commands: availableCommands(),
    });
    internal.renderWorkflowView();
    await new Promise<void>((resolve) => setImmediate(resolve));

    assert.strictEqual(htmlWrites, 2);
    assert.ok(html.includes("Failed delivery"));

    visible = false;
    internal.lastViewState = testWorkflowState({
      workspaceCount: 1,
      targetName: "Visible again",
      commands: availableCommands(),
    });
    internal.renderWorkflowView();
    assert.strictEqual(htmlWrites, 2);

    visible = true;
    internal.renderWorkflowView();
    assert.strictEqual(htmlWrites, 3);
    assert.ok(html.includes("Visible again"));
  });

  test("workflow regions are stable and escaped", () => {
    const regions = workflowViewRegions(
      testWorkflowState({ workspaceCount: 1, targetName: "<Host>" }),
    );

    assert.deepStrictEqual(Object.keys(regions), [
      "freecm-target",
      "freecm-workflow",
      "freecm-dependencies",
      "freecm-active-lock",
      "freecm-maintenance",
      "freecm-repo-commands",
      "freecm-code-count",
    ]);
    assert.ok(regions["freecm-target"].includes("&lt;Host&gt;"));
    assert.ok(!regions["freecm-target"].includes("<Host>"));
  });

  test("workspace cache invalidates only data owned by the changed file", () => {
    const state = new FreeCMWorkspaceState(() => undefined);
    const folder = { name: "Host", fsPath: "/repo/Host" };
    const entry = state.cacheForFolder(folder);
    entry.capabilities = {
      folder,
      hasSeedRepositories: true,
      hasWorkflowScript: true,
      hasLockFile: true,
      hasRepoCommandManifest: true,
    };
    entry.lockStatus = { mode: "pinned", unavailable: false };
    entry.dependencyComparison = {
      status: "empty",
      sampleMode: undefined,
      activeMode: undefined,
      rows: [],
    };
    entry.repoCommandManifest = undefined;
    entry.repoCommands = emptyTestRepoCommands();

    state.invalidateWatchedFile(folder.fsPath, "configs/freecm.commands.jsonc");
    let current = state.cacheForFolder(folder);

    assert.strictEqual(current.capabilities, undefined);
    assert.strictEqual(current.repoCommands, undefined);
    assert.deepStrictEqual(current.lockStatus, {
      mode: "pinned",
      unavailable: false,
    });

    current.repoCommands = emptyTestRepoCommands();
    state.invalidateWatchedFile(folder.fsPath, "source_roots.lock.jsonc");
    current = state.cacheForFolder(folder);
    assert.strictEqual(current.lockStatus, undefined);
    assert.strictEqual(current.dependencyComparison, undefined);
    assert.notStrictEqual(current.repoCommands, undefined);

    current.capabilities = {
      folder,
      hasSeedRepositories: false,
      hasWorkflowScript: true,
      hasLockFile: true,
      hasRepoCommandManifest: true,
    };
    state.invalidateWatchedFile(folder.fsPath, "build/dependency_seed_repos");
    current = state.cacheForFolder(folder);
    assert.strictEqual(current.capabilities, undefined);
    assert.notStrictEqual(current.repoCommands, undefined);
  });

  test("watched invalidation prevents stale async cache writeback", async () => {
    const folder = { name: "Host", fsPath: "/repo/Host" };
    let activeLockExists = false;
    let activeProbeCount = 0;
    let releaseFirstProbe: (() => void) | undefined;
    let markFirstProbeStarted: (() => void) | undefined;
    const firstProbeStarted = new Promise<void>((resolve) => {
      markFirstProbeStarted = resolve;
    });
    const firstProbeGate = new Promise<void>((resolve) => {
      releaseFirstProbe = resolve;
    });
    const fileSystem = {
      async exists(filePath: string): Promise<boolean> {
        if (filePath.endsWith("source_roots.lock.jsonc")) {
          activeProbeCount += 1;
          const result = activeLockExists;
          if (activeProbeCount === 1) {
            markFirstProbeStarted?.();
            await firstProbeGate;
          }
          return result;
        }
        return false;
      },
      async isDirectory(): Promise<boolean> {
        return false;
      },
    };
    const state = new FreeCMWorkspaceState(() => undefined, fileSystem);
    state.currentWorkspaceFolders = () => [folder];

    const staleRead = state.workspaceCapabilities();
    await firstProbeStarted;
    state.invalidateWatchedFile(folder.fsPath, "source_roots.lock.jsonc");
    activeLockExists = true;
    releaseFirstProbe?.();
    const stale = await staleRead;
    const fresh = await state.workspaceCapabilities();

    assert.strictEqual(stale[0].hasLockFile, false);
    assert.strictEqual(fresh[0].hasLockFile, true);
    assert.strictEqual(activeProbeCount, 2);
  });

  test("workflow view refreshes external manual status after its TTL", async () => {
    const repoRoot = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-manual-view-"),
    );
    const manualPath = path.join(repoRoot, "manual", "LibA");
    await fs.mkdir(manualPath, { recursive: true });
    execFileSync("git", ["init", "--quiet"], { cwd: manualPath });
    const dependency = {
      remote: "https://example.invalid/LibA.git",
      commit: "a".repeat(40),
    };
    await Promise.all([
      fs.writeFile(
        path.join(repoRoot, "source_roots.lock.jsonc.in"),
        JSON.stringify({
          schemaVersion: 5,
          depsMode: "pinned",
          dependencies: { LibA: dependency },
          depsManualPath: { LibA: "" },
        }),
      ),
      fs.writeFile(
        path.join(repoRoot, "source_roots.lock.jsonc"),
        JSON.stringify({
          schemaVersion: 5,
          depsMode: "manual",
          dependencies: { LibA: dependency },
          depsManualPath: { LibA: manualPath },
        }),
      ),
    ]);
    clearManualPathStatusCache();
    const folder = { name: "Host", fsPath: repoRoot };
    const workspaceState = new FreeCMWorkspaceState(() => undefined);
    const builder = new WorkflowViewStateBuilder(
      workspaceState,
      () => undefined,
    );

    const clean = await builder.readDependencyComparisonViewState(folder);
    await fs.writeFile(path.join(manualPath, "dirty.txt"), "dirty\n");
    const stillCached = await builder.readDependencyComparisonViewState(folder);
    await new Promise<void>((resolve) => setTimeout(resolve, 550));
    const refreshed = await builder.readDependencyComparisonViewState(folder);

    assert.strictEqual(clean.rows[0].activeManualPathStatus, "clean");
    assert.strictEqual(stillCached.rows[0].activeManualPathStatus, "clean");
    assert.strictEqual(refreshed.rows[0].activeManualPathStatus, "dirty");
    clearManualPathStatusCache();
  });

  test("terminal logging creates only the log terminal", () => {
    const manager = new TerminalSessionManager();
    const commandCount = vscode.window.terminals.filter(
      (terminal) => terminal.name === "FreeCM",
    ).length;
    manager.logToTerminal("info", "log-only", {
      name: "Host",
      fsPath: "/repo/Host",
    });
    const internal = manager as unknown as {
      logTerminal: vscode.Terminal | undefined;
    };

    assert.strictEqual(
      vscode.window.terminals.filter((terminal) => terminal.name === "FreeCM")
        .length,
      commandCount,
    );
    assert.strictEqual(internal.logTerminal?.name, "FreeCM Log");
    internal.logTerminal?.dispose();
  });

  test("workflow webview message protocol rejects unknown commands", () => {
    assert.strictEqual(isWorkflowMessage({ command: "update" }), true);
    assert.strictEqual(isWorkflowMessage({ command: "pullSeeds" }), true);
    assert.strictEqual(isWorkflowMessage({ command: "pullFreeCM" }), false);
    assert.strictEqual(isWorkflowMessage({ command: "selectPackage" }), true);
    assert.strictEqual(
      isWorkflowMessage({
        command: "saveCountExcludePaths",
        value: "build\nSources/Generated",
      }),
      true,
    );
    assert.strictEqual(
      isWorkflowMessage({
        command: "applyActiveDependencyToSample",
        dependency: "LibA",
      }),
      true,
    );
    assert.strictEqual(
      isWorkflowMessage({
        command: "manualDependency",
        dependency: "LibA",
      }),
      true,
    );
    assert.strictEqual(
      isWorkflowMessage({
        command: "restoreDependencyPin",
        dependency: "LibA",
      }),
      true,
    );
    assert.strictEqual(
      isWorkflowMessage({ command: "manualDependency" }),
      false,
    );
    assert.strictEqual(
      isWorkflowMessage({
        command: "applyActiveDependencyToSample",
        dependency: "../LibA",
      }),
      false,
    );
    assert.strictEqual(
      isWorkflowMessage({
        command: "pullSampleDependency",
        dependency: "LibA",
      }),
      false,
    );
    assert.strictEqual(
      isWorkflowMessage({ command: "saveCountExcludePaths" }),
      false,
    );
    assert.strictEqual(
      isWorkflowMessage({
        command: "saveCountExcludePaths",
        value: ["build"],
      }),
      false,
    );
    assert.strictEqual(
      isWorkflowMessage({ command: "addCountExcludeFolder" }),
      false,
    );
    assert.strictEqual(
      isWorkflowMessage({ command: "removeCountExcludeFolder" }),
      false,
    );
    assert.strictEqual(isWorkflowMessage({ command: "rm -rf ." }), false);
    assert.strictEqual(isWorkflowMessage({ command: ["update"] }), false);
    assert.strictEqual(isWorkflowMessage({}), false);
    assert.strictEqual(isWorkflowMessage(null), false);
  });

  test("workflow webview includes nonce-based content security policy", () => {
    const html = workflowViewHtml(
      testWorkflowState({
        workspaceCount: 1,
        targetName: "Host",
      }),
      {
        cspSource: "vscode-webview-resource:",
        nonce: "testNonce",
        scriptUri: "vscode-webview-resource:/workflow.js",
        styleUri: "vscode-webview-resource:/workflow.css",
      },
    );

    assert.ok(html.includes("Content-Security-Policy"));
    assert.ok(html.includes("default-src 'none'"));
    assert.ok(html.includes("style-src vscode-webview-resource:"));
    assert.ok(
      html.includes("script-src 'nonce-testNonce' vscode-webview-resource:"),
    );
    assert.ok(
      html.includes(
        '<link rel="stylesheet" href="vscode-webview-resource:/workflow.css">',
      ),
    );
    assert.ok(
      html.includes(
        '<script nonce="testNonce" src="vscode-webview-resource:/workflow.js"></script>',
      ),
    );
    assert.ok(!html.includes("<style"));
    assert.ok(!html.includes("<script>"));
    assert.ok(!html.includes("acquireVsCodeApi"));
  });

  test("workspace watchers use root-relative file patterns", () => {
    assert.deepStrictEqual(__test.WATCHED_WORKSPACE_FILES, [
      "build/dependency_seed_repos",
      "source_roots.lock.jsonc",
      "source_roots.lock.jsonc.in",
      "configs/freecm.commands.jsonc",
      "configs/source_root_workflow.py",
    ]);
    for (const pattern of __test.WATCHED_WORKSPACE_FILES) {
      assert.ok(!pattern.includes("**"));
    }
  });

  test("repo command action state exposes only explicit selections", () => {
    const variants: RepoCommandVariant[] = [
      {
        id: "default",
        label: "Default Build",
        command: "cmake",
        args: ["--build", "--preset", "release"],
        steps: [
          {
            command: "cmake",
            args: ["--build", "--preset", "release"],
          },
        ],
        default: true,
      },
      {
        id: "debug",
        label: "Debug Build",
        command: "cmake",
        args: ["--build", "--preset", "debug"],
        steps: [
          {
            command: "cmake",
            args: ["--build", "--preset", "debug"],
          },
        ],
      },
    ];

    assert.deepStrictEqual(
      repoCommandActionViewStateFromSelection("config", variants, undefined),
      {
        action: "config",
        enabled: true,
        selectedLabel: undefined,
        variantCount: 2,
      },
    );
    assert.deepStrictEqual(
      repoCommandActionViewStateFromSelection("config", variants, "missing"),
      {
        action: "config",
        enabled: true,
        selectedLabel: undefined,
        variantCount: 2,
      },
    );
    assert.deepStrictEqual(
      repoCommandActionViewStateFromSelection("config", variants, "debug"),
      {
        action: "config",
        enabled: true,
        selectedLabel: "Debug Build",
        variantCount: 2,
      },
    );
  });

  test("repo command action state is disabled without any compatible default", () => {
    assert.deepStrictEqual(
      repoCommandActionViewStateFromSelection("run", [], undefined),
      {
        action: "run",
        enabled: false,
        selectedLabel: undefined,
        variantCount: 0,
      },
    );
  });

  test("file path comparison follows platform casing", () => {
    assert.strictEqual(sameFilePath("/repo/app", "/repo/app", "darwin"), true);
    assert.strictEqual(
      sameFilePath("/repo/app", "/repo/other", "darwin"),
      false,
    );
    assert.strictEqual(
      sameFilePath("C:\\Repo\\App", "c:\\repo\\app", "win32"),
      true,
    );
  });

  test("disposed terminal errors are retryable", () => {
    assert.strictEqual(
      __test.isDisposedTerminalError(
        new Error("Terminal has already been disposed"),
      ),
      true,
    );
    assert.strictEqual(
      __test.isDisposedTerminalError("terminal has already been disposed"),
      true,
    );
    assert.strictEqual(
      __test.isDisposedTerminalError(new Error("Build failed")),
      false,
    );
  });

  test("workflow view groups dependency buttons under active lock", () => {
    const html = workflowViewHtml(
      testWorkflowState({
        workspaceCount: 1,
        targetName: "Host",
        launching: false,
        commands: availableCommands(),
        lockMode: "manual",
        lockStatusUnavailable: false,
        dependencyComparison: {
          status: "ready",
          sampleMode: "pinned",
          activeMode: "manual",
          rows: [
            {
              name: "LibA",
              samplePresent: true,
              sampleCommit: "111111111",
              activePresent: false,
              activeCommit: undefined,
              activeMode: undefined,
            },
            {
              name: "LibB",
              samplePresent: true,
              sampleCommit: "222222222",
              activePresent: true,
              activeCommit: "bbbbbbbbb",
              activeMode: "pinned",
            },
            {
              name: "LibC",
              samplePresent: false,
              sampleCommit: undefined,
              activePresent: true,
              activeCommit: "ccccccccc",
              activeMode: "manual",
              activeManualPath: "/repo/Host/custom/LibC",
              activeManualPathStatus: "dirty",
            },
          ],
        },
        repoCommands: {
          status: "missing",
          message: undefined,
          actions: {
            config: {
              action: "config",
              enabled: false,
              selectedLabel: undefined,
              variantCount: 1,
            },
            build: {
              action: "build",
              enabled: false,
              selectedLabel: undefined,
              variantCount: 0,
            },
            test: {
              action: "test",
              enabled: false,
              selectedLabel: undefined,
              variantCount: 0,
            },
            run: {
              action: "run",
              enabled: false,
              selectedLabel: undefined,
              variantCount: 0,
            },
            package: {
              action: "package",
              enabled: false,
              selectedLabel: undefined,
              variantCount: 0,
            },
          },
        },
        codeCount: {
          enabled: true,
          targetPath: "/repo/Host/Sources",
          targetLabel: "Sources",
          outputLabel: ".freecm/counts",
          excludePaths: ["generated", "DerivedData"],
        },
      }),
    );

    const activeLockIndex = html.indexOf("Active Lock");
    const templateLockIndex = html.indexOf("Template Lock");
    const usePinnedIndex = html.indexOf("Use pinned");
    const manualAllIndex = html.indexOf("Manual all");
    const pinLatestIndex = html.indexOf("Pin latest");
    const updateUsedIndex = html.indexOf("Update used");

    assert.ok(activeLockIndex >= 0);
    assert.strictEqual(templateLockIndex, -1);
    assert.ok(html.includes("source_roots.lock.jsonc"));
    assert.ok(activeLockIndex < usePinnedIndex);
    assert.ok(html.includes("Pull"));
    assert.ok(html.includes("Pull Seeds"));
    assert.ok(!html.includes("Pull Submodule"));
    assert.ok(usePinnedIndex < pinLatestIndex);
    assert.ok(pinLatestIndex < manualAllIndex);
    assert.ok(manualAllIndex < updateUsedIndex);
    assert.ok(html.includes("Maintenance"));
    assert.ok(html.includes("Clean build"));
    assert.ok(html.includes("Code Count"));
    assert.ok(html.includes("Sources"));
    assert.ok(html.includes('id="countCode"'));
    assert.ok(html.includes('id="changeCountPath"'));
    assert.ok(html.includes('id="resetCountPath"'));
    assert.ok(html.includes('id="editCountExcludePaths"'));
    assert.ok(html.includes('id="saveCountExcludePaths"'));
    assert.ok(html.includes('id="cancelCountExcludePaths"'));
    assert.ok(html.includes('id="countExcludePathsText"'));
    assert.ok(!html.includes('id="addCountExcludeFolder"'));
    assert.ok(!html.includes('id="removeCountExcludeFolder"'));
    assert.ok(html.includes('aria-label="Count code"'));
    assert.ok(html.includes('aria-label="Change code count path"'));
    assert.ok(html.includes('aria-label="Reset code count path"'));
    assert.ok(html.includes('aria-label="Edit code count excluded paths"'));
    assert.ok(html.includes('aria-label="Save code count excluded paths"'));
    assert.ok(
      html.includes('aria-label="Cancel code count excluded path edits"'),
    );
    assert.ok(html.includes("generated"));
    assert.ok(html.includes("DerivedData"));
    assert.ok(html.includes("Dependencies"));
    assert.ok(html.indexOf("Workflow") < html.indexOf("Dependencies"));
    assert.ok(html.indexOf("Dependencies") < html.indexOf("Active Lock"));
    assert.ok(html.indexOf("Project Commands") < html.indexOf("Code Count"));
    assert.ok(html.includes("Sample"));
    assert.ok(html.includes("Active"));
    assert.ok(html.includes("LibA"));
    assert.ok(html.includes("LibB"));
    assert.ok(html.includes("LibC"));
    assert.ok(html.includes(">1111111</span>"));
    assert.ok(html.includes(">2222222</span>"));
    assert.ok(html.includes(">bbbbbbb</span>"));
    assert.ok(
      !html.includes(
        'data-command="applyActiveDependencyToSample" data-dependency="LibA"',
      ),
    );
    assert.ok(
      html.includes(
        'data-command="applyActiveDependencyToSample" data-dependency="LibB"',
      ),
    );
    assert.ok(html.includes('title="Apply active LibB to sample"'));
    assert.ok(html.includes('aria-label="Apply active LibB to sample"'));
    assert.ok(html.includes("&lt;-</button>"));
    assert.ok(
      html.includes('data-command="manualDependency" data-dependency="LibB"'),
    );
    assert.ok(
      html.includes(
        'data-command="restoreDependencyPin" data-dependency="LibC"',
      ),
    );
    assert.ok(
      !html.includes(
        'data-command="manualDependency" data-dependency="LibC"',
      ),
    );
    assert.ok(html.includes(">M(dirty)</span>"));
    assert.ok(html.includes('class="dependency-state manual manual-dirty"'));
    assert.ok(html.includes("manual dirty: /repo/Host/custom/LibC"));
    assert.ok(html.includes('Dependency not present">-</span>'));
    assert.ok(html.includes("Config: Select..."));
    assert.ok(
      html.indexOf("Config: Select...") < html.indexOf("Build: Select..."),
    );
    assert.ok(
      html.indexOf("Build: Select...") < html.indexOf("Run: Select..."),
    );
    assert.ok(html.indexOf("Run: Select...") < html.indexOf("Test: Select..."));
    assert.ok(
      html.indexOf("Test: Select...") < html.indexOf("Package: Select..."),
    );
    assert.ok(!html.includes("Mode manual"));
    assert.ok(!html.includes(">Target</div>"));
    assert.ok(!html.includes("Ready"));
  });

  test("workflow view keeps code count enabled without a FreeCM workspace", () => {
    const html = workflowViewHtml(
      testWorkflowState({
        workspaceCount: 1,
        targetName: undefined,
        launching: false,
        commands: {
          ...emptyCommandAvailability(),
          pull: true,
          cleanBuild: true,
        },
        codeCount: {
          enabled: true,
          targetPath: "/repo/Plain",
          targetLabel: ".",
          outputLabel: ".freecm/counts",
          excludePaths: [],
        },
      }),
    );

    assert.ok(/id="init"[^>]*disabled/.test(html));
    assert.ok(/id="update"[^>]*disabled/.test(html));
    assert.ok(!/id="countCode"[^>]*disabled/.test(html));
    assert.ok(!/id="changeCountPath"[^>]*disabled/.test(html));
    assert.ok(!/id="resetCountPath"[^>]*disabled/.test(html));
    assert.ok(!/id="editCountExcludePaths"[^>]*disabled/.test(html));
    assert.ok(!html.includes('id="addCountExcludeFolder"'));
    assert.ok(!html.includes('id="removeCountExcludeFolder"'));
  });

  test("workflow view hides inactive code count exclude editor panel", async () => {
    const extension = vscode.extensions.getExtension("ethan-kang.freecm");
    assert.ok(extension, "extension should be discoverable");

    const css = await fs.readFile(
      path.join(extension.extensionPath, "resources", "workflow.css"),
      "utf8",
    );

    assert.match(
      css,
      /\.filter-preview\[hidden\],\s*\.filter-edit\[hidden\]\s*{/,
    );
    assert.match(
      css,
      /\.filter-preview\[hidden\],\s*\.filter-edit\[hidden\]\s*{[^}]*display:\s*none;/s,
    );
  });

  test("workflow view shows dependency status unavailable without blocking buttons", () => {
    const html = workflowViewHtml(
      testWorkflowState({
        workspaceCount: 1,
        targetName: "Host",
        launching: false,
        commands: {
          ...emptyCommandAvailability(),
          pull: true,
          init: true,
          update: true,
          cleanBuild: true,
          usePinned: true,
          manualAll: true,
          updateUsed: true,
        },
        lockMode: "pinned",
        lockStatusUnavailable: false,
        dependencyComparison: {
          status: "unavailable",
          sampleMode: undefined,
          activeMode: undefined,
          rows: [],
        },
        repoCommands: {
          status: "missing",
          message: undefined,
          actions: {
            config: {
              action: "config",
              enabled: false,
              selectedLabel: undefined,
              variantCount: 0,
            },
            build: {
              action: "build",
              enabled: false,
              selectedLabel: undefined,
              variantCount: 0,
            },
            test: {
              action: "test",
              enabled: false,
              selectedLabel: undefined,
              variantCount: 0,
            },
            run: {
              action: "run",
              enabled: false,
              selectedLabel: undefined,
              variantCount: 0,
            },
            package: {
              action: "package",
              enabled: false,
              selectedLabel: undefined,
              variantCount: 0,
            },
          },
        },
      }),
    );

    assert.ok(html.includes("Dependency status unavailable"));
    assert.ok(html.includes('id="init" class="primary" '));
    assert.ok(html.includes('id="update" class="primary" '));
  });

  test("workflow view disables dependency row buttons while launching", () => {
    const html = workflowViewHtml(
      testWorkflowState({
        launching: true,
        dependencyComparison: {
          status: "ready",
          sampleMode: "pinned",
          activeMode: "pinned",
          rows: [
            {
              name: "LibA",
              samplePresent: true,
              sampleCommit: "111111111",
              activePresent: true,
              activeCommit: "222222222",
              activeMode: "pinned",
            },
          ],
        },
        repoCommands: emptyTestRepoCommands(),
      }),
    );

    assert.match(
      html,
      /data-command="applyActiveDependencyToSample" data-dependency="LibA" disabled/,
    );
    assert.match(
      html,
      /data-command="manualDependency" data-dependency="LibA" disabled/,
    );
  });

  test("workflow view disables restore dependency buttons while launching", () => {
    const html = workflowViewHtml(
      testWorkflowState({
        launching: true,
        dependencyComparison: {
          status: "ready",
          sampleMode: "pinned",
          activeMode: "manual",
          rows: [
            {
              name: "LibA",
              samplePresent: true,
              sampleCommit: "111111111",
              activePresent: true,
              activeCommit: "222222222",
              activeMode: "manual",
              activeManualPath: "/repo/manual/LibA",
              activeManualPathStatus: "clean",
            },
          ],
        },
        repoCommands: emptyTestRepoCommands(),
      }),
    );

    assert.match(
      html,
      /data-command="restoreDependencyPin" data-dependency="LibA" disabled/,
    );
  });

  test("workflow view marks rows with mismatched pinned commits", () => {
    const html = workflowViewHtml(
      testWorkflowState({
        workspaceCount: 1,
        targetName: "Host",
        launching: false,
        commands: {
          ...emptyCommandAvailability(),
          usePinned: true,
          pinLatest: true,
          manualAll: true,
          updateUsed: true,
        },
        lockMode: "pinned",
        lockStatusUnavailable: false,
        dependencyComparison: {
          status: "ready",
          sampleMode: "pinned",
          activeMode: "pinned",
          rows: [
            {
              name: "SameLib",
              samplePresent: true,
              sampleCommit: "aaaaaaaaa",
              activePresent: true,
              activeCommit: "aaaaaaaaa",
              activeMode: "pinned",
            },
            {
              name: "ChangedLib",
              samplePresent: true,
              sampleCommit: "bbbbbbbbb",
              activePresent: true,
              activeCommit: "ccccccccc",
              activeMode: "pinned",
            },
          ],
        },
        repoCommands: emptyTestRepoCommands(),
      }),
    );

    assert.ok(html.includes('class="dependency-row mismatch"'));
    assert.ok(
      html.includes(
        "Pinned commit mismatch: sample bbbbbbbbb, active ccccccccc",
      ),
    );
    assert.ok(!html.includes("Pinned commit mismatch: sample aaaaaaaaa"));
  });

  test("workflow view renders manual dependency path statuses", () => {
    const html = workflowViewHtml(
      testWorkflowState({
        dependencyComparison: {
          status: "ready",
          sampleMode: "pinned",
          activeMode: "manual",
          rows: [
            {
              name: "CleanLib",
              samplePresent: true,
              sampleCommit: "111111111",
              activePresent: true,
              activeCommit: "aaaaaaaaa",
              activeMode: "manual",
              activeManualPath: "/repo/manual/CleanLib",
              activeManualPathStatus: "clean",
            },
            {
              name: "DirtyLib",
              samplePresent: true,
              sampleCommit: "222222222",
              activePresent: true,
              activeCommit: "bbbbbbbbb",
              activeMode: "manual",
              activeManualPath: "/repo/manual/DirtyLib",
              activeManualPathStatus: "dirty",
            },
            {
              name: "UnknownLib",
              samplePresent: true,
              sampleCommit: "333333333",
              activePresent: true,
              activeCommit: "ccccccccc",
              activeMode: "manual",
              activeManualPath: "/repo/manual/UnknownLib",
              activeManualPathStatus: "untracked",
            },
          ],
        },
        repoCommands: emptyTestRepoCommands(),
      }),
    );

    assert.ok(html.includes(">M(Clean)</span>"));
    assert.ok(html.includes(">M(dirty)</span>"));
    assert.ok(html.includes(">M(U)</span>"));
    assert.ok(html.includes('class="dependency-state manual manual-clean"'));
    assert.ok(html.includes('class="dependency-state manual manual-dirty"'));
    assert.ok(
      html.includes('class="dependency-state manual manual-untracked"'),
    );
    assert.ok(html.includes("manual clean: /repo/manual/CleanLib"));
    assert.ok(html.includes("manual dirty: /repo/manual/DirtyLib"));
    assert.ok(
      html.includes(
        "manual untracked or unavailable: /repo/manual/UnknownLib",
      ),
    );
  });

  test("panel repo command selectors defer QuickPick until after webview focus settles", async () => {
    const context = {
      subscriptions: [],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    const startedAt = Date.now();
    let selectedAt: number | undefined;

    (
      extension as unknown as {
        selectRepoCommand: (action: string) => Promise<void>;
      }
    ).selectRepoCommand = async (action: string) => {
      assert.strictEqual(action, "config");
      selectedAt = Date.now();
    };

    await extension.runPanelCommand("selectConfig");

    assert.ok(selectedAt !== undefined, "selector should run");
    assert.ok(
      selectedAt - startedAt >=
        __test.PANEL_QUICK_PICK_DELAY_MS - panelQuickPickDelayToleranceMs,
      "selector should wait for the webview click/focus event to finish",
    );
  });

  test("panel repo command selectors refresh the rendered button label", async () => {
    const context = {
      extensionUri: vscode.Uri.file("/repo/FreeCM/vscode-extension"),
      subscriptions: [],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    let renderedHtml: string | undefined;

    (
      extension as unknown as {
        workflowView: vscode.WebviewView;
      }
    ).workflowView = {
      webview: {
        cspSource: "vscode-webview-resource:",
        asWebviewUri: (uri: vscode.Uri) => uri,
        get html() {
          return renderedHtml ?? "";
        },
        set html(value: string) {
          renderedHtml = value;
        },
      },
    } as unknown as vscode.WebviewView;
    (
      extension as unknown as {
        selectRepoCommand: (action: string) => Promise<void>;
      }
    ).selectRepoCommand = async (action: string) => {
      assert.strictEqual(action, "config");
      const actions = emptyTestRepoCommands().actions;
      (
        extension as unknown as { lastViewState: WorkflowStateInput }
      ).lastViewState = testWorkflowState({
        workspaceCount: 1,
        targetName: "Host",
        commands: availableCommands(),
        repoCommands: {
          status: "ready",
          message: undefined,
          actions: {
            ...actions,
            config: {
              action: "config",
              enabled: true,
              selectedLabel: "Debug Project",
              variantCount: 2,
            },
          },
        },
      });
      (
        extension as unknown as { renderWorkflowView: () => void }
      ).renderWorkflowView();
      assert.strictEqual(renderedHtml, undefined);
    };

    await extension.runPanelCommand("selectConfig");

    assert.ok(renderedHtml?.includes("Config: Debug Project"));
  });

  test("panel package command selector maps to package action", async () => {
    const context = {
      subscriptions: [],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    let selectedAction: string | undefined;

    (
      extension as unknown as {
        selectRepoCommand: (action: string) => Promise<void>;
      }
    ).selectRepoCommand = async (action: string) => {
      selectedAction = action;
    };

    await extension.runPanelCommand("selectPackage");

    assert.strictEqual(selectedAction, "package");
  });

  test("repo command opens selector when no explicit variant is selected", async () => {
    const folder = { name: "Host", fsPath: "/repo/Host" };
    const variant = testRepoCommandVariant("debug", "Debug Project");
    const manifest = testRepoCommandManifest("config", [variant]);
    const context = {
      subscriptions: [],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    let selected:
      | { action: RepoCommandAction; folder: typeof folder | undefined }
      | undefined;
    let executed = false;
    class DelegatingRepoCommandController extends RepoCommandController {
      async selectRepoCommand(
        action: RepoCommandAction,
        options: Parameters<RepoCommandController["selectRepoCommand"]>[1] = {},
      ): Promise<void> {
        selected = { action, folder: options.folder };
      }
    }

    const internal = extension as unknown as {
      resolveTargetFolderWithCapability: () => Promise<typeof folder>;
      loadRepoCommandsForFolder: () => Promise<RepoCommandManifestState>;
      explicitRepoCommandVariant: () => RepoCommandVariant | undefined;
      executeInFreeCMTerminal: () => Promise<void>;
    };
    internal.resolveTargetFolderWithCapability = async () => folder;
    internal.loadRepoCommandsForFolder = async () => manifest;
    internal.explicitRepoCommandVariant = () => undefined;
    internal.executeInFreeCMTerminal = async () => {
      executed = true;
    };

    await new DelegatingRepoCommandController(extension as never).runRepoCommand(
      "config",
    );

    assert.deepStrictEqual(selected, { action: "config", folder });
    assert.strictEqual(executed, false);
  });

  test("repo command executes an explicit valid selection", async () => {
    const folder = { name: "Host", fsPath: "/repo/Host" };
    const variant = testRepoCommandVariant("debug", "Debug Project");
    const manifest = testRepoCommandManifest("config", [variant]);
    const context = {
      subscriptions: [],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    let selected = false;
    let executed:
      | { label: string; lines: readonly string[]; action: string }
      | undefined;

    const internal = extension as unknown as {
      resolveTargetFolderWithCapability: () => Promise<typeof folder>;
      loadRepoCommandsForFolder: () => Promise<RepoCommandManifestState>;
      explicitRepoCommandVariant: () => RepoCommandVariant | undefined;
      selectRepoCommand: () => Promise<void>;
      terminalForRepoCommand: (
        target: typeof folder,
        action: string,
      ) => Promise<vscode.Terminal>;
      executeInFreeCMTerminal: (
        target: typeof folder,
        label: string,
        terminalFactory: () => vscode.Terminal | Promise<vscode.Terminal>,
        lines: readonly string[],
      ) => Promise<void>;
      refresh: () => Promise<void>;
      runRepoCommand: (action: string) => Promise<void>;
    };
    internal.resolveTargetFolderWithCapability = async () => folder;
    internal.loadRepoCommandsForFolder = async () => manifest;
    internal.explicitRepoCommandVariant = () => variant;
    internal.selectRepoCommand = async () => {
      selected = true;
    };
    internal.terminalForRepoCommand = async (_target, action) =>
      ({ name: action } as vscode.Terminal);
    internal.executeInFreeCMTerminal = async (
      _target,
      label,
      terminalFactory,
      lines,
    ) => {
      const terminal = await terminalFactory();
      executed = { label, lines, action: terminal.name };
    };
    internal.refresh = async () => undefined;

    await internal.runRepoCommand("config");

    assert.strictEqual(selected, false);
    assert.deepStrictEqual(executed, {
      label: "Config: Debug Project",
      lines: ["cmake --build --preset debug"],
      action: "config",
    });
  });

  test("panel code count exclude paths migrate legacy state and save from webview", async () => {
    const folder = { name: "Host", fsPath: "/repo/Host" };
    const state = new Map<string, unknown>();
    const context = {
      subscriptions: [],
      workspaceState: {
        get: (key: string) => state.get(key),
        update: async (key: string, value: unknown) => {
          if (value === undefined) {
            state.delete(key);
          } else {
            state.set(key, value);
          }
        },
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    const legacyKey = __test.codeCountExcludeFoldersKey(folder);
    const key = __test.codeCountExcludePathsKey(folder);
    const originalShowWarningMessage = vscode.window.showWarningMessage;
    try {
      (
        extension as unknown as {
          resolveTargetFolderForCodeCount: () => Promise<typeof folder>;
          refresh: () => Promise<void>;
        }
      ).resolveTargetFolderForCodeCount = async () => folder;
      (
        extension as unknown as {
          refresh: () => Promise<void>;
        }
      ).refresh = async () => undefined;
      state.set(legacyKey, ["Generated"]);
      assert.deepStrictEqual(
        (
          extension as unknown as {
            codeCountExcludePaths: (target: typeof folder) => string[];
          }
        ).codeCountExcludePaths(folder),
        ["build", "FreeCM", "thirdparty", "Downloads", "Generated"],
      );

      let warning: string | undefined;
      (
        vscode.window as unknown as {
          showWarningMessage: typeof vscode.window.showWarningMessage;
        }
      ).showWarningMessage = async (message: string) => {
        warning = message;
        return undefined;
      };

      await extension.runPanelMessage({
        command: "saveCountExcludePaths",
        value: "build\nSources\\Generated\nGenerated\ngenerated\n",
      });

      assert.strictEqual(warning, undefined);
      assert.deepStrictEqual(state.get(key), [
        "build",
        "Sources/Generated",
        "Generated",
      ]);
      assert.strictEqual(state.has(legacyKey), false);

      await extension.runPanelMessage({
        command: "saveCountExcludePaths",
        value: "",
      });

      assert.deepStrictEqual(state.get(key), []);
      assert.deepStrictEqual(
        (
          extension as unknown as {
            codeCountExcludePaths: (target: typeof folder) => string[];
          }
        ).codeCountExcludePaths(folder),
        [],
      );

      await extension.runPanelMessage({
        command: "saveCountExcludePaths",
        value: "build\n*.tmp",
      });

      assert.strictEqual(
        warning,
        "Line 2: Wildcards and negation are not supported in exclude paths.",
      );
      assert.deepStrictEqual(state.get(key), []);
    } finally {
      (
        vscode.window as unknown as {
          showWarningMessage: typeof vscode.window.showWarningMessage;
        }
      ).showWarningMessage = originalShowWarningMessage;
    }
  });

  test("panel repo command primary actions defer selection when no variant is selected", async () => {
    const context = {
      subscriptions: [],
      workspaceState: {
        get: () => undefined,
        update: async () => undefined,
      },
    } as unknown as vscode.ExtensionContext;
    const extension = new __test.FreeCMExtension(context);
    const startedAt = Date.now();
    let selectedAt: number | undefined;

    (
      extension as unknown as {
        runRepoCommand: (action: string) => Promise<void>;
      }
    ).runRepoCommand = async (action: string) => {
      assert.strictEqual(action, "config");
      await new Promise((resolve) =>
        setTimeout(resolve, __test.PANEL_QUICK_PICK_DELAY_MS),
      );
      selectedAt = Date.now();
    };

    await extension.runPanelCommand("config");

    assert.ok(
      selectedAt !== undefined,
      "primary action should still reach selector",
    );
    assert.ok(
      selectedAt - startedAt >=
        __test.PANEL_QUICK_PICK_DELAY_MS - panelQuickPickDelayToleranceMs,
      "primary action should also wait for the webview click/focus event to finish",
    );
  });
});

function emptyTestRepoCommands() {
  return {
    status: "missing" as const,
    message: undefined,
    actions: {
      config: {
        action: "config" as const,
        enabled: false,
        selectedLabel: undefined,
        variantCount: 0,
      },
      build: {
        action: "build" as const,
        enabled: false,
        selectedLabel: undefined,
        variantCount: 0,
      },
      test: {
        action: "test" as const,
        enabled: false,
        selectedLabel: undefined,
        variantCount: 0,
      },
      run: {
        action: "run" as const,
        enabled: false,
        selectedLabel: undefined,
        variantCount: 0,
      },
      package: {
        action: "package" as const,
        enabled: false,
        selectedLabel: undefined,
        variantCount: 0,
      },
    },
  };
}

function testRepoCommandVariant(
  id: string,
  label: string,
): RepoCommandVariant {
  const args = ["--build", "--preset", id];
  return {
    id,
    label,
    command: "cmake",
    args,
    steps: [{ command: "cmake", args }],
  };
}

function testRepoCommandManifest(
  action: RepoCommandAction,
  variants: readonly RepoCommandVariant[],
): RepoCommandManifestState {
  const actionState = (
    name: RepoCommandAction,
  ): RepoCommandManifestState["actions"][RepoCommandAction] => ({
    action: name,
    variants: name === action ? variants : [],
    defaultVariant: name === action ? variants[0] : undefined,
  });
  return {
    manifestPath: "/repo/Host/configs/freecm.commands.jsonc",
    actions: {
      config: actionState("config"),
      build: actionState("build"),
      run: actionState("run"),
      test: actionState("test"),
      package: actionState("package"),
    },
  };
}

type WorkflowStateInput = Parameters<typeof workflowViewHtml>[0];

function testWorkflowState(
  overrides: Partial<WorkflowStateInput>,
): WorkflowStateInput {
  return {
    workspaceCount: 0,
    targetName: undefined,
    launching: false,
    commands: emptyCommandAvailability(),
    lockMode: undefined,
    lockStatusUnavailable: false,
    dependencyComparison: {
      status: "empty",
      sampleMode: undefined,
      activeMode: undefined,
      rows: [],
    },
    repoCommands: emptyTestRepoCommands(),
    codeCount: {
      enabled: false,
      targetPath: undefined,
      targetLabel: undefined,
      outputLabel: undefined,
      excludePaths: [],
    },
    ...overrides,
  };
}

function emptyCommandAvailability(): WorkflowStateInput["commands"] {
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

function availableCommands(): WorkflowStateInput["commands"] {
  return {
    pull: true,
    pullSeeds: true,
    init: true,
    update: true,
    cleanBuild: true,
    usePinned: true,
    pinLatest: true,
    manualAll: true,
    updateUsed: true,
  };
}
