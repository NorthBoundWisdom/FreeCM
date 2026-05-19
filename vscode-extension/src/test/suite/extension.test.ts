import * as assert from "assert";
import * as vscode from "vscode";
import {
  __test,
  repoCommandActionViewStateFromSelection,
  workflowViewHtml,
} from "../../extension";
import { RepoCommandVariant } from "../../repoCommands";

suite("extension", () => {
  test("activates and registers workflow commands", async () => {
    const extension = vscode.extensions.getExtension(
      "northboundwisdom.freecm-vscode",
    );
    assert.ok(extension, "extension should be discoverable");

    await extension.activate();
    const commands = await vscode.commands.getCommands(true);

    assert.ok(commands.includes("freecm.init"));
    assert.ok(commands.includes("freecm.pull"));
    assert.ok(commands.includes("freecm.pullFreeCM"));
    assert.ok(commands.includes("freecm.update"));
    assert.ok(commands.includes("freecm.cleanBuild"));
    assert.ok(commands.includes("freecm.config"));
    assert.ok(commands.includes("freecm.build"));
    assert.ok(commands.includes("freecm.test"));
    assert.ok(commands.includes("freecm.run"));
  });

  test("contributes the workflow webview", async () => {
    const extension = vscode.extensions.getExtension(
      "northboundwisdom.freecm-vscode",
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

  test("repo command action state requires an explicit compatible selection", () => {
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
        enabled: false,
        selectedLabel: undefined,
        variantCount: 2,
      },
    );
    assert.deepStrictEqual(
      repoCommandActionViewStateFromSelection("config", variants, "missing"),
      {
        action: "config",
        enabled: false,
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

  test("workflow view groups dependency buttons under active lock", () => {
    const html = workflowViewHtml({
      eligibleFolders: [{ name: "Host", fsPath: "/repo/Host" }],
      targetName: "Host",
      launching: false,
      lockMode: "manual",
      lockStatusUnavailable: false,
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
        },
      },
    });

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
    assert.ok(html.includes("Config: Select..."));
    assert.ok(html.indexOf("Config: Select...") < html.indexOf("Build: Select..."));
    assert.ok(html.indexOf("Build: Select...") < html.indexOf("Run: Select..."));
    assert.ok(html.indexOf("Run: Select...") < html.indexOf("Test: Select..."));
    assert.ok(html.includes("Mode manual"));
    assert.ok(!html.includes("Ready"));
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
      selectedAt - startedAt >= __test.PANEL_QUICK_PICK_DELAY_MS,
      "selector should wait for the webview click/focus event to finish",
    );
  });
});
