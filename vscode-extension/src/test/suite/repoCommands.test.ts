import * as assert from "assert";
import {
  commandLineForTerminal,
  commandLinesForTerminal,
  compatibleRepoCommandVariants,
  defaultRepoCommandVariant,
  isRepoCommandVariantCompatible,
  parseRepoCommandManifest,
  repoCommandWarnings,
} from "../../repoCommands";

const MANIFEST_PATH = "/repo/configs/freecm.commands.jsonc";

suite("repo commands", () => {
  test("parses Config-scoped JSONC command variants", () => {
    const manifest = parseRepoCommandManifest(
      `{
        // Repo command surface
        "version": 2,
        "commands": {
          "config": [
            {
              "id": "mac-release",
              "label": "Mac Release",
              "command": "cmake",
              "args": ["--preset", "mac_clang_release"],
              "platforms": ["darwin"],
              "default": true,
              "defaults": {
                "build": "mac-build",
                "test": "precommit",
                "package": "mac-package"
              },
              "readiness": {
                "inputs": ["source_roots.lock.jsonc", "CMakePresets.json"],
                "outputs": ["build/mac_clang_release/CMakeCache.txt"]
              }
            }
          ],
          "build": [
            {
              "id": "mac-build",
              "label": "Mac Build",
              "command": "cmake",
              "args": ["--build", "--preset", "mac_clang_release"],
              "configurations": ["mac-release"]
            }
          ],
          "test": [
            {
              "id": "precommit",
              "label": "Precommit",
              "description": "Runs the default precommit suite",
              "command": "python3",
              "args": ["configs/ios_workflow.py", "test", "--level", "precommit"],
              "configurations": ["mac-release"]
            }
          ],
          "run": [],
          "package": [
            {
              "id": "mac-package",
              "label": "Mac Package",
              "description": "Build and package a distributable macOS app",
              "command": "python3",
              "args": ["configs/ios_workflow.py", "package", "--platform", "mac"],
              "configurations": ["mac-release"]
            }
          ]
        }
      }`,
      MANIFEST_PATH,
      "darwin",
    );

    assert.strictEqual(manifest.configurations.length, 1);
    assert.strictEqual(manifest.defaultConfiguration?.id, "mac-release");
    assert.deepStrictEqual(manifest.defaultConfiguration?.readiness, {
      inputs: ["source_roots.lock.jsonc", "CMakePresets.json"],
      outputs: ["build/mac_clang_release/CMakeCache.txt"],
    });
    assert.deepStrictEqual(
      compatibleRepoCommandVariants(manifest, "mac-release", "build").map(
        (variant) => variant.id,
      ),
      ["mac-build"],
    );
    assert.strictEqual(
      defaultRepoCommandVariant(manifest, "mac-release", "test")?.description,
      "Runs the default precommit suite",
    );
    assert.strictEqual(
      defaultRepoCommandVariant(manifest, "mac-release", "package")?.id,
      "mac-package",
    );
    assert.strictEqual(
      defaultRepoCommandVariant(manifest, "mac-release", "run"),
      undefined,
    );
  });

  test("filters Configs by platform and keeps shared downstream variants", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 2,
        commands: {
          config: [
            configVariant("mac", ["darwin"], {
              package: "source-package",
            }),
            configVariant(
              "linux-release",
              ["linux"],
              { package: "source-package" },
              true,
            ),
            configVariant(
              "linux-debug",
              ["linux"],
              { package: "source-package" },
              false,
            ),
          ],
          package: [
            {
              id: "source-package",
              label: "Source Package",
              command: "python3",
              args: ["configs/package.py"],
              configurations: ["mac", "linux-release", "linux-debug"],
            },
          ],
        },
      }),
      MANIFEST_PATH,
      "linux",
    );

    assert.deepStrictEqual(
      manifest.configurations.map((variant) => variant.id),
      ["linux-release", "linux-debug"],
    );
    assert.strictEqual(manifest.defaultConfiguration?.id, "linux-release");
    assert.deepStrictEqual(
      manifest.actions.package.variants.map((variant) => variant.id),
      ["source-package"],
    );
    assert.strictEqual(
      defaultRepoCommandVariant(
        manifest,
        "linux-debug",
        "package",
      )?.id,
      "source-package",
    );
  });

  test("parses multi-step downstream variants", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 2,
        commands: {
          config: [
            configVariant("mac", ["darwin"], {
              package: "mac-dmg",
            }),
          ],
          package: [
            {
              id: "mac-dmg",
              label: "Mac DMG",
              configurations: ["mac"],
              steps: [
                {
                  command: "python3",
                  args: [
                    "configs/ios_workflow.py",
                    "build",
                    "--configuration",
                    "Release",
                  ],
                },
                {
                  command: "python3",
                  args: [
                    "configs/ios_workflow.py",
                    "dmg",
                    "--configuration",
                    "Release",
                  ],
                },
              ],
            },
          ],
        },
      }),
      MANIFEST_PATH,
      "darwin",
    );

    assert.deepStrictEqual(
      commandLinesForTerminal(
        defaultRepoCommandVariant(manifest, "mac", "package")!,
      ),
      [
        "python3 configs/ios_workflow.py build --configuration Release",
        "python3 configs/ios_workflow.py dmg --configuration Release",
      ],
    );
  });

  test("parses multi-step Config variants", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 2,
        commands: {
          config: [
            {
              id: "xcode-sync",
              label: "Xcode Sync",
              platforms: ["darwin"],
              default: true,
              defaults: {},
              steps: [
                {
                  command: "python3",
                  args: ["configs/xcodeproj_workflow.py", "sync"],
                },
                {
                  command: "python3",
                  args: ["configs/xcodeproj_workflow.py", "verify"],
                },
              ],
            },
          ],
        },
      }),
      MANIFEST_PATH,
      "darwin",
    );

    assert.deepStrictEqual(
      commandLinesForTerminal(manifest.defaultConfiguration!),
      [
        "python3 configs/xcodeproj_workflow.py sync",
        "python3 configs/xcodeproj_workflow.py verify",
      ],
    );
  });

  test("rejects manifest version 1", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({ version: 1, commands: {} }),
          MANIFEST_PATH,
          "darwin",
        ),
      /version must be 2/,
    );
  });

  test("rejects shell string args", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                {
                  id: "mac",
                  label: "Mac",
                  command: "cmake --preset mac",
                  args: "--fresh",
                  platforms: ["darwin"],
                  default: true,
                  defaults: {},
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /commands\.config\[0\]\.args must be a string array/,
    );
  });

  test("rejects variants mixing command args and steps", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                {
                  id: "mixed",
                  label: "Mixed",
                  command: "cmake",
                  args: ["--preset", "mac"],
                  steps: [{ command: "cmake", args: ["--fresh"] }],
                  platforms: ["darwin"],
                  default: true,
                  defaults: {},
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /must use either command\/args or steps, not both/,
    );
  });

  test("rejects empty multi-step variants", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                {
                  id: "empty",
                  label: "Empty",
                  steps: [],
                  platforms: ["darwin"],
                  default: true,
                  defaults: {},
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /commands\.config\[0\]\.steps must be a non-empty array/,
    );
  });

  test("rejects duplicate variant ids per action", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                configVariant("same", ["darwin"], {}),
                configVariant("same", ["linux"], {}),
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /commands\.config contains duplicate id "same"/,
    );
  });

  test("rejects unsupported Config platforms", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                configVariant("ios", ["macos"], {}),
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /unsupported platform "macos"/,
    );
  });

  test("rejects Config-only fields on downstream variants", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                configVariant("mac", ["darwin"], { build: "build" }),
              ],
              build: [
                {
                  id: "build",
                  label: "Build",
                  command: "cmake",
                  args: ["--build"],
                  configurations: ["mac"],
                  platforms: ["darwin"],
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /platforms is only valid for Config variants/,
    );
  });

  test("requires downstream configuration references", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                configVariant("mac", ["darwin"], { build: "build" }),
              ],
              build: [
                {
                  id: "build",
                  label: "Build",
                  command: "cmake",
                  args: ["--build"],
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /configurations must be a string array/,
    );
  });

  test("rejects unknown Config references", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [configVariant("mac", ["darwin"], {})],
              run: [
                {
                  id: "app",
                  label: "App",
                  command: "./build/app",
                  args: [],
                  configurations: ["missing"],
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /references unknown Config "missing"/,
    );
  });

  test("requires a default for every compatible downstream action", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [configVariant("mac", ["darwin"], {})],
              build: [
                {
                  id: "build",
                  label: "Build",
                  command: "cmake",
                  args: ["--build"],
                  configurations: ["mac"],
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /must declare defaults\.build/,
    );
  });

  test("rejects downstream defaults from another Config", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                configVariant("release", ["darwin"], { build: "debug-build" }),
                configVariant(
                  "debug",
                  ["darwin"],
                  { build: "debug-build" },
                  false,
                ),
              ],
              build: [
                {
                  id: "debug-build",
                  label: "Debug Build",
                  command: "cmake",
                  args: ["--build", "--preset", "debug"],
                  configurations: ["debug"],
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /defaults\.build references "debug-build", but no build variants support that Config/,
    );
  });

  test("requires exactly one default Config per supported platform", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                configVariant("release", ["darwin"], {}, false),
                configVariant("debug", ["darwin"], {}, false),
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /platform darwin must have exactly one default Config; found 0/,
    );
  });

  test("rejects duplicate configuration references", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                configVariant("mac", ["darwin"], { run: "app" }),
              ],
              run: [
                {
                  id: "app",
                  label: "App",
                  command: "./build/app",
                  args: [],
                  configurations: ["mac", "mac"],
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /configurations contains duplicate value "mac"/,
    );
  });

  test("rejects readiness paths outside the repository", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 2,
            commands: {
              config: [
                {
                  ...configVariant("mac", ["darwin"], {}),
                  readiness: {
                    inputs: ["../shared/CMakePresets.json"],
                  },
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /must stay within the repository/,
    );
  });

  test("reports compatibility directly", () => {
    assert.strictEqual(
      isRepoCommandVariantCompatible(
        {
          id: "run",
          label: "Run",
          configurations: ["release"],
          steps: [{ command: "./app", args: [] }],
        },
        "release",
      ),
      true,
    );
    assert.strictEqual(
      isRepoCommandVariantCompatible(
        {
          id: "run",
          label: "Run",
          configurations: ["release"],
          steps: [{ command: "./app", args: [] }],
        },
        "debug",
      ),
      false,
    );
  });

  test("quotes terminal command arguments", () => {
    assert.strictEqual(
      commandLineForTerminal(
        {
          id: "run",
          label: "Run",
          steps: [
            {
              command: "python3",
              args: [
                "configs/ios_workflow.py",
                "test",
                "--destination",
                "platform=macOS,arch=arm64",
                "--only-testing",
                "Suite/Test Case",
              ],
            },
          ],
        },
        "darwin",
      ),
      "python3 configs/ios_workflow.py test --destination 'platform=macOS,arch=arm64' --only-testing 'Suite/Test Case'",
    );
  });

  test("quotes Windows terminal command arguments", () => {
    assert.strictEqual(
      commandLineForTerminal(
        {
          id: "run",
          label: "Run",
          steps: [
            {
              command: "python",
              args: [
                "configs\\windows_workflow.py",
                "run",
                "--name",
                "Debug App",
              ],
            },
          ],
        },
        "win32",
      ),
      'python configs\\windows_workflow.py run --name "Debug App"',
    );
  });

  test("warns when macOS run opens a detached app bundle", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 2,
        commands: {
          config: [
            configVariant("mac", ["darwin"], { run: "mac-app" }),
          ],
          run: [
            {
              id: "mac-app",
              label: "Mac App",
              command: "open",
              args: ["build/mac/Example.app"],
              configurations: ["mac"],
            },
          ],
        },
      }),
      MANIFEST_PATH,
      "darwin",
    );

    const warnings = repoCommandWarnings(manifest, "darwin");
    assert.strictEqual(warnings.length, 1);
    assert.strictEqual(warnings[0].action, "run");
    assert.strictEqual(warnings[0].variantId, "mac-app");
    assert.match(warnings[0].message, /detaches from the terminal/);
    assert.match(warnings[0].message, /\.app\/Contents\/MacOS/);
  });
});

function configVariant(
  id: string,
  platforms: readonly string[],
  defaults: Record<string, string>,
  isDefault: boolean = true,
): Record<string, unknown> {
  return {
    id,
    label: id,
    command: "cmake",
    args: ["--preset", id],
    platforms,
    default: isDefault,
    defaults,
  };
}
