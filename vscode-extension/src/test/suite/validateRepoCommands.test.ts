import * as assert from "assert";
import { spawn } from "child_process";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";

async function createRepoRoot(manifest: unknown): Promise<string> {
  const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-command-cli-"));
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
      version: 1,
      commands: {
        run: [
          {
            id: "mac-app",
            label: "Mac App",
            steps: [
              {
                command: "cmake",
                args: ["--build", "--preset", "mac_clang_debug", "--target", "DwgAtlas"],
              },
              {
                command: "./build/mac app/DwgAtlas",
                args: [],
              },
            ],
          },
        ],
      },
    });

    const result = await runValidator(["--preview", "--platform", "darwin", repoRoot]);

    assert.strictEqual(result.code, 0);
    assert.match(result.stdout, /Run: Mac App/);
    assert.match(result.stdout, /cmake --build --preset mac_clang_debug --target DwgAtlas/);
    assert.match(result.stdout, /\.\/build\/mac app\/DwgAtlas'/);
  });

  test("prints detach warning for open app run commands", async () => {
    const repoRoot = await createRepoRoot({
      version: 1,
      commands: {
        run: [
          {
            id: "bad-app",
            label: "Bad App",
            command: "open",
            args: ["build/mac/DwgAtlas.app"],
            platforms: ["darwin"],
          },
        ],
      },
    });

    const result = await runValidator(["--preview", "--platform", "darwin", repoRoot]);

    assert.strictEqual(result.code, 0);
    assert.match(result.stdout, /open build\/mac\/DwgAtlas.app/);
    assert.match(result.stderr, /warning: run:bad-app step 1:/);
    assert.match(result.stderr, /detaches from the terminal/);
  });

  test("exits non-zero when the manifest is invalid", async () => {
    const repoRoot = await createRepoRoot({
      version: 1,
      commands: {
        build: [
          {
            id: "bad",
            label: "Bad",
            command: "cmake",
            args: "--build --preset mac",
          },
        ],
      },
    });

    const result = await runValidator([repoRoot]);

    assert.strictEqual(result.code, 1);
    assert.match(result.stderr, /commands\.build\[0\]\.args must be a string array/);
  });
});

