import * as assert from "assert";
import {
  activeRepoCommandConfiguration,
  emptyRepoCommandSelectionState,
  repoCommandSelectionState,
  repoCommandVariantsForSelection,
  selectedRepoCommandVariant,
  withSelectedRepoCommandVariant,
} from "../../repoCommandState";
import {
  RepoCommandManifestState,
  parseRepoCommandManifest,
} from "../../repoCommands";

suite("repo command state", () => {
  test("uses Config defaults before explicit selection", () => {
    const manifest = testManifest();
    const state = emptyRepoCommandSelectionState();

    assert.strictEqual(
      activeRepoCommandConfiguration(manifest, state)?.id,
      "release",
    );
    assert.strictEqual(
      selectedRepoCommandVariant(manifest, state, "build")?.id,
      "release-build",
    );
    assert.strictEqual(
      selectedRepoCommandVariant(manifest, state, "run")?.id,
      "release-app",
    );
  });

  test("scopes downstream selections to each Config", () => {
    const manifest = testManifest();
    let state = emptyRepoCommandSelectionState();
    state = withSelectedRepoCommandVariant(
      manifest,
      state,
      "run",
      "release-tool",
    );
    state = withSelectedRepoCommandVariant(manifest, state, "config", "debug");
    state = withSelectedRepoCommandVariant(
      manifest,
      state,
      "run",
      "debug-tool",
    );

    assert.strictEqual(
      selectedRepoCommandVariant(manifest, state, "run")?.id,
      "debug-tool",
    );
    state = withSelectedRepoCommandVariant(
      manifest,
      state,
      "config",
      "release",
    );
    assert.strictEqual(
      selectedRepoCommandVariant(manifest, state, "run")?.id,
      "release-tool",
    );
  });

  test("filters picker variants through the active Config", () => {
    const manifest = testManifest();
    const state = withSelectedRepoCommandVariant(
      manifest,
      emptyRepoCommandSelectionState(),
      "config",
      "debug",
    );

    assert.deepStrictEqual(
      repoCommandVariantsForSelection(manifest, state, "run").map(
        (variant) => variant.id,
      ),
      ["debug-app", "debug-tool"],
    );
  });

  test("rejects incompatible downstream selections", () => {
    const manifest = testManifest();
    assert.throws(
      () =>
        withSelectedRepoCommandVariant(
          manifest,
          emptyRepoCommandSelectionState(),
          "run",
          "debug-app",
        ),
      /not compatible with Config "release"/,
    );
  });

  test("ignores invalid persisted state and removed variants", () => {
    const manifest = testManifest();
    const state = repoCommandSelectionState({
      version: 2,
      activeConfigId: "removed",
      selectionsByConfig: {
        release: {
          run: "removed-run",
        },
      },
      readinessByConfig: {},
    });

    assert.strictEqual(
      activeRepoCommandConfiguration(manifest, state)?.id,
      "release",
    );
    assert.strictEqual(
      selectedRepoCommandVariant(manifest, state, "run")?.id,
      "release-app",
    );
  });

  test("does not read legacy independent selection state", () => {
    assert.deepStrictEqual(
      repoCommandSelectionState({
        version: 1,
        activeConfigId: "release",
      }),
      emptyRepoCommandSelectionState(),
    );
  });
});

function testManifest(): RepoCommandManifestState {
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
            platforms: ["darwin"],
            default: true,
            defaults: {
              build: "release-build",
              run: "release-app",
            },
          },
          {
            id: "debug",
            label: "Debug",
            command: "cmake",
            args: ["--preset", "debug"],
            platforms: ["darwin"],
            defaults: {
              build: "debug-build",
              run: "debug-app",
            },
          },
        ],
        build: [
          commandVariant("release-build", ["release"]),
          commandVariant("debug-build", ["debug"]),
        ],
        run: [
          commandVariant("release-app", ["release"]),
          commandVariant("release-tool", ["release"]),
          commandVariant("debug-app", ["debug"]),
          commandVariant("debug-tool", ["debug"]),
        ],
      },
    }),
    "/repo/configs/freecm.commands.jsonc",
    "darwin",
  );
}

function commandVariant(
  id: string,
  configurations: readonly string[],
): Record<string, unknown> {
  return {
    id,
    label: id,
    command: `./${id}`,
    args: [],
    configurations,
  };
}
