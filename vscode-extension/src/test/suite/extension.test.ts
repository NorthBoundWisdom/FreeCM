import * as assert from "assert";
import * as vscode from "vscode";
import {
  __test,
  repoCommandActionViewStateFromSelection,
  sameFilePath,
  workflowViewHtml,
} from "../../extension";
import { RepoCommandVariant } from "../../repoCommands";
import { isWorkflowMessage } from "../../webview/messageProtocol";

suite("extension", () => {
  const panelQuickPickDelayToleranceMs = 20;

  test("activates and registers workflow commands", async () => {
    const extension = vscode.extensions.getExtension(
      "ethan-kang.freecm",
    );
    assert.ok(extension, "extension should be discoverable");

    await extension.activate();
    const commands = await vscode.commands.getCommands(true);
    const activationEvents = extension.packageJSON.activationEvents as string[];

    assert.ok(!activationEvents.includes("onStartupFinished"));
    assert.ok(activationEvents.includes("workspaceContains:configs/source_root_workflow.py"));
    assert.ok(activationEvents.includes("workspaceContains:configs/freecm.commands.jsonc"));
    assert.ok(activationEvents.includes("workspaceContains:source_roots.lock.jsonc"));
    assert.ok(activationEvents.includes("workspaceContains:source_roots.lock.jsonc.in"));
    assert.ok(commands.includes("freecm.init"));
    assert.ok(commands.includes("freecm.pull"));
    assert.ok(commands.includes("freecm.pullFreeCM"));
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
    const extension = vscode.extensions.getExtension(
      "ethan-kang.freecm",
    );
    assert.ok(extension, "extension should be discoverable");

    const packageJson = extension.packageJSON as {
      contributes?: {
        views?: Record<string, Array<{ id: string; name: string; type?: string }>>;
        viewsContainers?: {
          activitybar?: Array<{ id: string; title: string; icon: string }>;
        };
      };
    };

    assert.deepStrictEqual(packageJson.contributes?.viewsContainers?.activitybar, [
      {
        id: "freecm",
        title: "FreeCM",
        icon: "resources/freecm.svg",
      },
    ]);
    assert.deepStrictEqual(packageJson.contributes?.views?.freecm, [
      {
        id: "freecm.workflow",
        name: "Workflow",
        type: "webview",
      },
    ]);
  });

  test("workflow webview releases hidden context", () => {
    assert.strictEqual(__test.RETAIN_WORKFLOW_WEBVIEW_CONTEXT_WHEN_HIDDEN, false);
  });

  test("workflow webview message protocol rejects unknown commands", () => {
    assert.strictEqual(isWorkflowMessage({ command: "update" }), true);
    assert.strictEqual(isWorkflowMessage({ command: "selectPackage" }), true);
    assert.strictEqual(isWorkflowMessage({ command: "rm -rf ." }), false);
    assert.strictEqual(isWorkflowMessage({ command: ["update"] }), false);
    assert.strictEqual(isWorkflowMessage({}), false);
    assert.strictEqual(isWorkflowMessage(null), false);
  });

  test("workflow webview includes nonce-based content security policy", () => {
    const html = workflowViewHtml(testWorkflowState({
      workspaceCount: 1,
      targetName: "Host",
    }), {
      cspSource: "vscode-webview-resource:",
      nonce: "testNonce",
      scriptUri: "vscode-webview-resource:/workflow.js",
      styleUri: "vscode-webview-resource:/workflow.css",
    });

    assert.ok(html.includes("Content-Security-Policy"));
    assert.ok(html.includes("default-src 'none'"));
    assert.ok(html.includes("style-src vscode-webview-resource:"));
    assert.ok(html.includes("script-src 'nonce-testNonce' vscode-webview-resource:"));
    assert.ok(html.includes('<link rel="stylesheet" href="vscode-webview-resource:/workflow.css">'));
    assert.ok(html.includes('<script nonce="testNonce" src="vscode-webview-resource:/workflow.js"></script>'));
    assert.ok(!html.includes("<style"));
    assert.ok(!html.includes("<script>"));
    assert.ok(!html.includes("acquireVsCodeApi"));
  });

  test("workspace watchers use root-relative file patterns", () => {
    assert.deepStrictEqual(__test.WATCHED_WORKSPACE_FILES, [
      "FreeCM",
      "source_roots.lock.jsonc",
      "source_roots.lock.jsonc.in",
      "configs/freecm.commands.jsonc",
      "configs/source_root_workflow.py",
    ]);
    for (const pattern of __test.WATCHED_WORKSPACE_FILES) {
      assert.ok(!pattern.includes("**"));
    }
  });

  test("repo command action state uses default until explicit selection", () => {
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
      repoCommandActionViewStateFromSelection(
        "config",
        variants,
        undefined,
        variants[0],
      ),
      {
        action: "config",
        enabled: true,
        selectedLabel: "Default Build",
        variantCount: 2,
      },
    );
    assert.deepStrictEqual(
      repoCommandActionViewStateFromSelection(
        "config",
        variants,
        "missing",
        variants[0],
      ),
      {
        action: "config",
        enabled: true,
        selectedLabel: "Default Build",
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
      repoCommandActionViewStateFromSelection("run", [], undefined, undefined),
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
    assert.strictEqual(sameFilePath("/repo/app", "/repo/other", "darwin"), false);
    assert.strictEqual(
      sameFilePath("C:\\Repo\\App", "c:\\repo\\app", "win32"),
      true,
    );
  });

  test("disposed terminal errors are retryable", () => {
    assert.strictEqual(
      __test.isDisposedTerminalError(new Error("Terminal has already been disposed")),
      true,
    );
    assert.strictEqual(
      __test.isDisposedTerminalError("terminal has already been disposed"),
      true,
    );
    assert.strictEqual(__test.isDisposedTerminalError(new Error("Build failed")), false);
  });

  test("workflow view groups dependency buttons under active lock", () => {
    const html = workflowViewHtml(testWorkflowState({
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
      },
    }));

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
    assert.ok(html.includes("Pull Submodule"));
    assert.ok(usePinnedIndex < pinLatestIndex);
    assert.ok(pinLatestIndex < manualAllIndex);
    assert.ok(manualAllIndex < updateUsedIndex);
    assert.ok(html.includes("Maintenance"));
    assert.ok(html.includes("Clean build"));
    assert.ok(html.includes("Code Count"));
    assert.ok(html.includes("Sources"));
    assert.ok(html.includes("id=\"countCode\""));
    assert.ok(html.includes("id=\"changeCountPath\""));
    assert.ok(html.includes("id=\"resetCountPath\""));
    assert.ok(html.includes('aria-label="Count code"'));
    assert.ok(html.includes('aria-label="Change code count path"'));
    assert.ok(html.includes('aria-label="Reset code count path"'));
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
    assert.ok(html.includes(">manual</span>"));
    assert.ok(html.includes("Dependency not present\">-</span>"));
    assert.ok(html.includes("Config: Select..."));
    assert.ok(html.indexOf("Config: Select...") < html.indexOf("Build: Select..."));
    assert.ok(html.indexOf("Build: Select...") < html.indexOf("Run: Select..."));
    assert.ok(html.indexOf("Run: Select...") < html.indexOf("Test: Select..."));
    assert.ok(html.indexOf("Test: Select...") < html.indexOf("Package: Select..."));
    assert.ok(!html.includes("Mode manual"));
    assert.ok(!html.includes(">Target</div>"));
    assert.ok(!html.includes("Ready"));
  });

  test("workflow view keeps code count enabled without a FreeCM workspace", () => {
    const html = workflowViewHtml(testWorkflowState({
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
      },
    }));

    assert.ok(/id="init"[^>]*disabled/.test(html));
    assert.ok(/id="update"[^>]*disabled/.test(html));
    assert.ok(!/id="countCode"[^>]*disabled/.test(html));
    assert.ok(!/id="changeCountPath"[^>]*disabled/.test(html));
    assert.ok(!/id="resetCountPath"[^>]*disabled/.test(html));
  });

  test("workflow view shows dependency status unavailable without blocking buttons", () => {
    const html = workflowViewHtml(testWorkflowState({
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
    }));

    assert.ok(html.includes("Dependency status unavailable"));
    assert.ok(html.includes("id=\"init\" class=\"primary\" "));
    assert.ok(html.includes("id=\"update\" class=\"primary\" "));
  });

  test("workflow view marks rows with mismatched pinned commits", () => {
    const html = workflowViewHtml(testWorkflowState({
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
    }));

    assert.ok(html.includes('class="dependency-row mismatch"'));
    assert.ok(html.includes("Pinned commit mismatch: sample bbbbbbbbb, active ccccccccc"));
    assert.ok(!html.includes("Pinned commit mismatch: sample aaaaaaaaa"));
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

    (extension as unknown as {
      selectRepoCommand: (action: string) => Promise<void>;
    }).selectRepoCommand = async (action: string) => {
      assert.strictEqual(action, "config");
      selectedAt = Date.now();
    };

    await extension.runPanelCommand("selectConfig");

    assert.ok(selectedAt !== undefined, "selector should run");
    assert.ok(
      selectedAt - startedAt
        >= __test.PANEL_QUICK_PICK_DELAY_MS - panelQuickPickDelayToleranceMs,
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

    (extension as unknown as {
      workflowView: vscode.WebviewView;
    }).workflowView = {
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
    (extension as unknown as {
      selectRepoCommand: (action: string) => Promise<void>;
    }).selectRepoCommand = async (action: string) => {
      assert.strictEqual(action, "config");
      const actions = emptyTestRepoCommands().actions;
      (extension as unknown as { lastViewState: WorkflowStateInput }).lastViewState =
        testWorkflowState({
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
      (extension as unknown as { renderWorkflowView: () => void }).renderWorkflowView();
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

    (extension as unknown as {
      selectRepoCommand: (action: string) => Promise<void>;
    }).selectRepoCommand = async (action: string) => {
      selectedAction = action;
    };

    await extension.runPanelCommand("selectPackage");

    assert.strictEqual(selectedAction, "package");
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

    (extension as unknown as {
      runRepoCommand: (action: string) => Promise<void>;
    }).runRepoCommand = async (action: string) => {
      assert.strictEqual(action, "config");
      await new Promise((resolve) => setTimeout(resolve, __test.PANEL_QUICK_PICK_DELAY_MS));
      selectedAt = Date.now();
    };

    await extension.runPanelCommand("config");

    assert.ok(selectedAt !== undefined, "primary action should still reach selector");
    assert.ok(
      selectedAt - startedAt
        >= __test.PANEL_QUICK_PICK_DELAY_MS - panelQuickPickDelayToleranceMs,
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
    },
    ...overrides,
  };
}

function emptyCommandAvailability(): WorkflowStateInput["commands"] {
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

function availableCommands(): WorkflowStateInput["commands"] {
  return {
    pull: true,
    pullFreeCM: true,
    init: true,
    update: true,
    cleanBuild: true,
    usePinned: true,
    pinLatest: true,
    manualAll: true,
    updateUsed: true,
  };
}
