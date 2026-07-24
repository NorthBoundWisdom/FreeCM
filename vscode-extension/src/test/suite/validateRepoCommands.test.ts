import * as assert from "assert";
import { spawn } from "child_process";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";

async function createRepoRoot(manifest: unknown): Promise<string> {
  const repoRoot = await fs.mkdtemp(
    path.join(os.tmpdir(), "freecm-command-cli-"),
  );
  await fs.mkdir(path.join(repoRoot, "configs"));
  await fs.writeFile(
    path.join(repoRoot, "configs", "freecm.commands.jsonc"),
    `${JSON.stringify(manifest, null, 2)}\n`,
    "utf8",
  );
  return repoRoot;
}

async function runValidator(args: readonly string[]): Promise<{
  code: number | null;
  stdout: string;
  stderr: string;
}> {
  const script = path.resolve(__dirname, "../../validateRepoCommands.js");
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script, ...args], {
      cwd: path.resolve(__dirname, "../../../.."),
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", reject);
    child.on("close", (code) => {
      resolve({ code, stdout, stderr });
    });
  });
}

suite("validate repo commands CLI", () => {
  test("prints preview with extension terminal quoting", async () => {
    const repoRoot = await createRepoRoot({
      version: 2,
      commands: {
        config: [
          {
            id: "mac-debug",
            label: "Mac Debug",
            command: "cmake",
            args: ["--preset", "mac_clang_debug"],
            platforms: ["darwin"],
            default: true,
            defaults: {
              run: "mac-app",
              package: "mac-dmg",
            },
          },
        ],
        run: [
          {
            id: "mac-app",
            label: "Mac App",
            configurations: ["mac-debug"],
            steps: [
              {
                command: "cmake",
                args: [
                  "--build",
                  "--preset",
                  "mac_clang_debug",
                  "--target",
                  "SampleApp",
                ],
              },
              {
                command: "./build/mac app/SampleApp",
                args: [],
              },
            ],
          },
        ],
        package: [
          {
            id: "mac-dmg",
            label: "Mac DMG",
            command: "python3",
            args: [
              "configs/ios_workflow.py",
              "package",
              "--configuration",
              "Release",
            ],
            configurations: ["mac-debug"],
          },
        ],
      },
    });

    const result = await runValidator([
      "--preview",
      "--platform",
      "darwin",
      repoRoot,
    ]);

    assert.strictEqual(result.code, 0);
    assert.match(result.stdout, /Configuration: Mac Debug \(default\)/);
    assert.match(result.stdout, /Run: Mac App \(default\)/);
    assert.match(
      result.stdout,
      /cmake --build --preset mac_clang_debug --target SampleApp/,
    );
    assert.match(result.stdout, /\.\/build\/mac app\/SampleApp'/);
    assert.match(result.stdout, /Package: Mac DMG/);
    assert.match(
      result.stdout,
      /python3 configs\/ios_workflow.py package --configuration Release/,
    );
  });

  test("prints detach warning for open app run commands", async () => {
    const repoRoot = await createRepoRoot({
      version: 2,
      commands: {
        config: [
          {
            id: "mac",
            label: "Mac",
            command: "cmake",
            args: ["--preset", "mac"],
            platforms: ["darwin"],
            default: true,
            defaults: {
              run: "bad-app",
            },
          },
        ],
        run: [
          {
            id: "bad-app",
            label: "Bad App",
            command: "open",
            args: ["build/mac/SampleApp.app"],
            configurations: ["mac"],
          },
        ],
      },
    });

    const result = await runValidator([
      "--preview",
      "--platform",
      "darwin",
      repoRoot,
    ]);

    assert.strictEqual(result.code, 0);
    assert.match(result.stdout, /open build\/mac\/SampleApp.app/);
    assert.match(result.stderr, /warning: run:bad-app step 1:/);
    assert.match(result.stderr, /detaches from the terminal/);
  });

  test("exits non-zero when the manifest is invalid", async () => {
    const repoRoot = await createRepoRoot({
      version: 2,
      commands: {
        config: [
          {
            id: "mac",
            label: "Mac",
            command: "cmake",
            args: ["--preset", "mac"],
            platforms: ["darwin"],
            default: true,
            defaults: {
              build: "bad",
            },
          },
        ],
        build: [
          {
            id: "bad",
            label: "Bad",
            command: "cmake",
            args: "--build --preset mac",
            configurations: ["mac"],
          },
        ],
      },
    });

    const result = await runValidator([repoRoot]);

    assert.strictEqual(result.code, 1);
    assert.match(
      result.stderr,
      /commands\.build\[0\]\.args must be a string array/,
    );
  });
});
