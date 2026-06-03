import * as assert from "assert";
import {
  commandLineForTerminal,
  commandLinesForTerminal,
  parseRepoCommandManifest,
  repoCommandWarnings,
} from "../../repoCommands";

const MANIFEST_PATH = "/repo/configs/freecm.commands.jsonc";

suite("repo commands", () => {
  test("parses JSONC command variants", () => {
    const manifest = parseRepoCommandManifest(
      `{
        // Repo command surface
        "version": 1,
        "commands": {
          "config": [
            {
              "id": "mac-config",
              "label": "Mac Config",
              "command": "cmake",
              "args": ["--preset", "mac_clang_release"],
              "platforms": ["darwin"],
              "default": true
            }
          ],
          "build": [
            {
              "id": "mac-release",
              "label": "Mac Release",
              "command": "cmake",
              "args": ["--build", "--preset", "mac_clang_release"],
              "platforms": ["darwin"],
              "default": true
            }
          ],
          "test": [
            {
              "id": "precommit",
              "label": "Precommit",
              "description": "Runs the default precommit suite",
              "command": "python3",
              "args": ["configs/ios_workflow.py", "test", "--level", "precommit"]
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
              "platforms": ["darwin"]
            }
          ]
        }
      }`,
      MANIFEST_PATH,
      "darwin",
    );

    assert.strictEqual(manifest.actions.config.variants.length, 1);
    assert.strictEqual(
      manifest.actions.config.defaultVariant?.id,
      "mac-config",
    );
    assert.strictEqual(manifest.actions.build.variants.length, 1);
    assert.strictEqual(
      manifest.actions.build.defaultVariant?.id,
      "mac-release",
    );
    assert.deepStrictEqual(manifest.actions.build.defaultVariant?.steps, [
      {
        command: "cmake",
        args: ["--build", "--preset", "mac_clang_release"],
      },
    ]);
    assert.strictEqual(
      manifest.actions.test.defaultVariant?.description,
      "Runs the default precommit suite",
    );
    assert.strictEqual(manifest.actions.run.defaultVariant, undefined);
    assert.strictEqual(
      manifest.actions.package.defaultVariant?.id,
      "mac-package",
    );
    assert.strictEqual(
      manifest.actions.package.defaultVariant?.description,
      "Build and package a distributable macOS app",
    );
  });

  test("keeps package action optional for old manifests", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 1,
        commands: {
          build: [
            {
              id: "mac-release",
              label: "Mac Release",
              command: "cmake",
              args: ["--build", "--preset", "mac_clang_release"],
            },
          ],
        },
      }),
      MANIFEST_PATH,
      "darwin",
    );

    assert.strictEqual(manifest.actions.package.variants.length, 0);
    assert.strictEqual(manifest.actions.package.defaultVariant, undefined);
  });

  test("parses package multi-step argv variants", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 1,
        commands: {
          package: [
            {
              id: "mac-dmg",
              label: "Mac DMG",
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
      commandLinesForTerminal(manifest.actions.package.defaultVariant!),
      [
        "python3 configs/ios_workflow.py build --configuration Release",
        "python3 configs/ios_workflow.py dmg --configuration Release",
      ],
    );
  });

  test("parses multi-step argv variants", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 1,
        commands: {
          build: [
            {
              id: "cmake-release",
              label: "CMake Release",
              steps: [
                {
                  command: "cmake",
                  args: ["--preset", "mac_clang_release"],
                },
                {
                  command: "cmake",
                  args: ["--build", "--preset", "mac_clang_release"],
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
      commandLinesForTerminal(manifest.actions.build.defaultVariant!),
      [
        "cmake --preset mac_clang_release",
        "cmake --build --preset mac_clang_release",
      ],
    );
  });

  test("parses config multi-step argv variants", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 1,
        commands: {
          config: [
            {
              id: "xcode-sync",
              label: "Xcode Sync",
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
      commandLinesForTerminal(manifest.actions.config.defaultVariant!),
      [
        "python3 configs/xcodeproj_workflow.py sync",
        "python3 configs/xcodeproj_workflow.py verify",
      ],
    );
  });

  test("filters variants by platform", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 1,
        commands: {
          build: [
            {
              id: "mac",
              label: "Mac",
              command: "cmake",
              args: ["--build", "--preset", "mac_clang_release"],
              platforms: ["darwin"],
            },
            {
              id: "win",
              label: "Windows",
              command: "python",
              args: ["configs/windows_workflow.py", "build"],
              platforms: ["win32"],
            },
          ],
        },
      }),
      MANIFEST_PATH,
      "win32",
    );

    assert.deepStrictEqual(
      manifest.actions.build.variants.map((variant) => variant.id),
      ["win"],
    );
  });

  test("default variant wins over first compatible variant", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 1,
        commands: {
          build: [
            {
              id: "first",
              label: "First",
              command: "cmake",
              args: ["--build", "--preset", "debug"],
            },
            {
              id: "default",
              label: "Default",
              command: "cmake",
              args: ["--build", "--preset", "release"],
              default: true,
            },
          ],
        },
      }),
      MANIFEST_PATH,
      "darwin",
    );

    assert.strictEqual(manifest.actions.build.defaultVariant?.id, "default");
  });

  test("first compatible variant is default when no explicit default exists", () => {
    const manifest = parseRepoCommandManifest(
      JSON.stringify({
        version: 1,
        commands: {
          run: [
            {
              id: "first",
              label: "First",
              command: "./build/app",
              args: [],
            },
          ],
        },
      }),
      MANIFEST_PATH,
      "darwin",
    );

    assert.strictEqual(manifest.actions.run.defaultVariant?.id, "first");
  });

  test("rejects shell string args", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 1,
            commands: {
              build: [
                {
                  id: "shell",
                  label: "Shell",
                  command: "cmake --build --preset mac",
                  args: "--target DownstreamProduct",
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /commands\.build\[0\]\.args must be a string array/,
    );
  });

  test("rejects variants mixing command args and steps", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 1,
            commands: {
              build: [
                {
                  id: "mixed",
                  label: "Mixed",
                  command: "cmake",
                  args: ["--preset", "mac_clang_release"],
                  steps: [
                    {
                      command: "cmake",
                      args: ["--build", "--preset", "mac_clang_release"],
                    },
                  ],
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
            version: 1,
            commands: {
              test: [
                {
                  id: "empty",
                  label: "Empty",
                  steps: [],
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /commands\.test\[0\]\.steps must be a non-empty array/,
    );
  });

  test("reports missing required fields", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 1,
            commands: {
              build: [
                {
                  id: "missing-command",
                  label: "Missing Command",
                  args: [],
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /commands\.build\[0\]\.command must be a non-empty string/,
    );
  });

  test("rejects duplicate variant ids per action", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 1,
            commands: {
              build: [
                {
                  id: "same",
                  label: "First",
                  command: "cmake",
                  args: ["--build", "--preset", "debug"],
                },
                {
                  id: "same",
                  label: "Second",
                  command: "cmake",
                  args: ["--build", "--preset", "release"],
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /commands\.build contains duplicate id "same"/,
    );
  });

  test("rejects unsupported platforms", () => {
    assert.throws(
      () =>
        parseRepoCommandManifest(
          JSON.stringify({
            version: 1,
            commands: {
              run: [
                {
                  id: "ios",
                  label: "iOS",
                  command: "python3",
                  args: ["configs/ios_workflow.py", "run"],
                  platforms: ["macos"],
                },
              ],
            },
          }),
          MANIFEST_PATH,
          "darwin",
        ),
      /unsupported platform "macos"/,
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
        version: 1,
        commands: {
          run: [
            {
              id: "mac-app",
              label: "Mac App",
              command: "open",
              args: ["build/mac/Example.app"],
              platforms: ["darwin"],
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
