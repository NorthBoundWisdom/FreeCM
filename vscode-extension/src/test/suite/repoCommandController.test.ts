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
import { TerminalCommandOutcome } from "../../terminal/terminalSessionManager";

suite("repo command controller", () => {
  test("requires Config success and invalidates readiness when inputs change", async () => {
    const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-command-"));
    await fs.writeFile(
      path.join(repoRoot, "CMakePresets.json"),
      '{"version": 6}\n',
    );
    const folder = { name: "Host", fsPath: repoRoot };
    const manifest = testManifest(repoRoot);
    let state = emptyRepoCommandSelectionState();
    let launching = false;
    const executed: string[] = [];
    const logs: Array<{ level: string; message: string }> = [];
    const host = {
      workspaceState: {
        invalidateCache: () => undefined,
      },
      isLaunching: () => launching,
      setLaunching: (value: boolean) => {
        launching = value;
      },
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
      terminalForRepoCommand: async () => ({}) as vscode.Terminal,
      executeInFreeCMTerminal: async (
        _folder: typeof folder,
        label: string,
      ): Promise<TerminalCommandOutcome> => {
        executed.push(label);
        return { status: "success", exitCode: 0 };
      },
      logToTerminal: (level: string, message: string) => {
        logs.push({ level, message });
      },
      finishTerminalLogGroup: () => undefined,
    } as unknown as CommandControllerHost;
    const controller = new RepoCommandController(host);

    await controller.runRepoCommand("build");
    assert.deepStrictEqual(executed, []);
    assert.ok(
      logs.some(
        ({ message }) => message === "Needs Config — Run Config: Release",
      ),
    );

    await controller.runRepoCommand("config");
    assert.deepStrictEqual(executed, ["Config: Release"]);
    assert.ok(state.readinessByConfig.release);

    await controller.runRepoCommand("build");
    assert.deepStrictEqual(executed, [
      "Config: Release",
      "Build: Release",
    ]);

    await fs.writeFile(
      path.join(repoRoot, "CMakePresets.json"),
      '{"version": 7}\n',
    );
    await controller.runRepoCommand("build");
    assert.deepStrictEqual(executed, [
      "Config: Release",
      "Build: Release",
    ]);
    assert.ok(
      logs.some(({ message }) =>
        message.includes("Config inputs changed; rerun Config: Release"),
      ),
    );
  });

  test("clears an earlier readiness receipt before rerunning Config", async () => {
    const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-command-"));
    await fs.writeFile(
      path.join(repoRoot, "CMakePresets.json"),
      '{"version": 6}\n',
    );
    const folder = { name: "Host", fsPath: repoRoot };
    const manifest = testManifest(repoRoot);
    let state: RepoCommandSelectionState = {
      ...emptyRepoCommandSelectionState(),
      readinessByConfig: {
        release: {
          signature: "old",
          completedAt: new Date().toISOString(),
        },
      },
    };
    const host = {
      workspaceState: {
        invalidateCache: () => undefined,
      },
      isLaunching: () => false,
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
      terminalForRepoCommand: async () => ({}) as vscode.Terminal,
      executeInFreeCMTerminal: async (): Promise<TerminalCommandOutcome> => ({
        status: "failure",
        exitCode: 1,
      }),
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
