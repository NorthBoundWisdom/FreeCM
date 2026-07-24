import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import {
  repoCommandConfigurationSignature,
  repoCommandReadinessStatus,
} from "../../repoCommandReadiness";
import { RepoCommandVariant } from "../../repoCommands";

suite("repo command readiness", () => {
  test("requires a successful Config receipt", async () => {
    const repoRoot = await testRepo();
    const configuration = testConfiguration();
    const status = await repoCommandReadinessStatus(
      repoRoot,
      configuration,
      undefined,
    );

    assert.strictEqual(status.ready, false);
    assert.match(status.reason ?? "", /Run Config: Mac Release/);
  });

  test("accepts a matching receipt and output marker", async () => {
    const repoRoot = await testRepo();
    const configuration = testConfiguration();
    const signature = await repoCommandConfigurationSignature(
      repoRoot,
      configuration,
    );
    const status = await repoCommandReadinessStatus(repoRoot, configuration, {
      signature,
      completedAt: new Date().toISOString(),
    });

    assert.strictEqual(status.ready, true);
  });

  test("invalidates readiness when an input changes", async () => {
    const repoRoot = await testRepo();
    const configuration = testConfiguration();
    const signature = await repoCommandConfigurationSignature(
      repoRoot,
      configuration,
    );
    await fs.writeFile(
      path.join(repoRoot, "CMakePresets.json"),
      '{"version": 7}\n',
    );

    const status = await repoCommandReadinessStatus(repoRoot, configuration, {
      signature,
      completedAt: new Date().toISOString(),
    });

    assert.strictEqual(status.ready, false);
    assert.match(status.reason ?? "", /Config inputs changed/);
  });

  test("invalidates readiness when an output marker is missing", async () => {
    const repoRoot = await testRepo();
    const configuration = testConfiguration();
    const signature = await repoCommandConfigurationSignature(
      repoRoot,
      configuration,
    );
    await fs.rm(path.join(repoRoot, "build", "release", "CMakeCache.txt"));

    const status = await repoCommandReadinessStatus(repoRoot, configuration, {
      signature,
      completedAt: new Date().toISOString(),
    });

    assert.strictEqual(status.ready, false);
    assert.match(status.reason ?? "", /Config output is missing/);
  });
});

async function testRepo(): Promise<string> {
  const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-ready-"));
  await fs.mkdir(path.join(repoRoot, "build", "release"), {
    recursive: true,
  });
  await Promise.all([
    fs.writeFile(path.join(repoRoot, "CMakePresets.json"), '{"version": 6}\n'),
    fs.writeFile(
      path.join(repoRoot, "build", "release", "CMakeCache.txt"),
      "ready\n",
    ),
  ]);
  return repoRoot;
}

function testConfiguration(): RepoCommandVariant {
  return {
    id: "mac-release",
    label: "Mac Release",
    steps: [
      {
        command: "cmake",
        args: ["--preset", "mac_clang_release"],
      },
    ],
    readiness: {
      inputs: ["CMakePresets.json"],
      outputs: ["build/release/CMakeCache.txt"],
    },
  };
}
