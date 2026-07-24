import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import { CommandControllerHost } from "../../controllers/commandHost";
import { RepoCommandController } from "../../controllers/repoCommandController";
import {
  emptyRepoCommandSelectionState,
  RepoCommandSelectionState,
  selectedRepoCommandVariant,
} from "../../repoCommandState";
import {
  parseRepoCommandManifest,
  RepoCommandAction,
} from "../../repoCommands";

suite("repo command controller", () => {
  test("queues Config and Build in order without a launch gate", async () => {
    const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-command-"));
    await fs.writeFile(
      path.join(repoRoot, "CMakePresets.json"),
      '{"version": 6}\n',
    );
    const folder = { name: "Host", fsPath: repoRoot };
    const manifest = testManifest(repoRoot);
    let state = emptyRepoCommandSelectionState();
    const queued: string[] = [];
    const logs: Array<{ level: string; message: string }> = [];
    const host = {
      workspaceState: {
        invalidateCache: () => undefined,
      },
      isLaunching: () => true,
      setLaunching: () => undefined,
      setStatusBarLaunchCommand: () => undefined,
      refresh: async () => undefined,
      resolveTargetFolderWithCapability: async () => folder,
      loadRepoCommandsForFolder: async () => manifest,
      repoCommandSelectionState: () => state,
      updateRepoCommandSelectionState: async (
        _folder: typeof folder,
        nextState: RepoCommandSelectionState,
      ) => {
        state = nextState;
      },
      selectedRepoCommandVariant: (
        _folder: typeof folder,
        _manifest: typeof manifest,
        action: RepoCommandAction,
      ) => selectedRepoCommandVariant(manifest, state, action),
      terminalForRepoCommand: async () => ({} as vscode.Terminal),
      queueInFreeCMTerminal: async (
        _folder: typeof folder,
        terminalFactory: () => Promise<vscode.Terminal>,
        lines: readonly string[],
      ) => {
        await terminalFactory();
        queued.push(lines.join(" && "));
      },
      logToTerminal: (level: string, message: string) => {
        logs.push({ level, message });
      },
      finishTerminalLogGroup: () => undefined,
    } as unknown as CommandControllerHost;
    const controller = new RepoCommandController(host);

    await controller.runRepoCommand("build");
    assert.deepStrictEqual(queued, []);
    assert.ok(
      logs.some(
        ({ message }) => message === "Needs Config — Run Config: Release",
      ),
    );

    const config = controller.runRepoCommand("config");
    const build = controller.runRepoCommand("build");
    await Promise.all([config, build]);

    assert.deepStrictEqual(queued, [
      "cmake --preset release",
      "cmake --build --preset release",
    ]);
    assert.ok(state.readinessByConfig.release?.submittedAt);
    assert.ok(
      logs.some(
        ({ message }) => message === "Queued Config: Release",
      ),
    );

    await fs.writeFile(
      path.join(repoRoot, "CMakePresets.json"),
      '{"version": 7}\n',
    );
    await controller.runRepoCommand("build");
    assert.deepStrictEqual(queued, [
      "cmake --preset release",
      "cmake --build --preset release",
    ]);
    assert.ok(
      logs.some(({ message }) =>
        message.includes("Config inputs changed; rerun Config: Release"),
      ),
    );
  });

  test("does not record Config submission when terminal delivery fails", async () => {
    const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-command-"));
    await fs.writeFile(
      path.join(repoRoot, "CMakePresets.json"),
      '{"version": 6}\n',
    );
    const folder = { name: "Host", fsPath: repoRoot };
    const manifest = testManifest(repoRoot);
    let state = emptyRepoCommandSelectionState();
    const host = {
      workspaceState: {
        invalidateCache: () => undefined,
      },
      refresh: async () => undefined,
      resolveTargetFolderWithCapability: async () => folder,
      loadRepoCommandsForFolder: async () => manifest,
      repoCommandSelectionState: () => state,
      updateRepoCommandSelectionState: async (
        _folder: typeof folder,
        nextState: RepoCommandSelectionState,
      ) => {
        state = nextState;
      },
      selectedRepoCommandVariant: (
        _folder: typeof folder,
        _manifest: typeof manifest,
        action: RepoCommandAction,
      ) => selectedRepoCommandVariant(manifest, state, action),
      terminalForRepoCommand: async () => ({} as vscode.Terminal),
      queueInFreeCMTerminal: async () => {
        throw new Error("terminal unavailable");
      },
      logToTerminal: () => undefined,
      finishTerminalLogGroup: () => undefined,
    } as unknown as CommandControllerHost;

    await new RepoCommandController(host).runRepoCommand("config");

    assert.strictEqual(state.readinessByConfig.release, undefined);
  });
});

function testManifest(repoRoot: string) {
  return parseRepoCommandManifest(
    JSON.stringify({
      version: 2,
      commands: {
        config: [
          {
            id: "release",
            label: "Release",
            command: "cmake",
            args: ["--preset", "release"],
            default: true,
            defaults: {
              build: "release",
            },
            readiness: {
              inputs: ["CMakePresets.json"],
            },
          },
        ],
        build: [
          {
            id: "release",
            label: "Release",
            command: "cmake",
            args: ["--build", "--preset", "release"],
            configurations: ["release"],
          },
        ],
      },
    }),
    path.join(repoRoot, "configs", "freecm.commands.jsonc"),
    process.platform,
  );
}
