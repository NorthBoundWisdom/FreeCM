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
  test("requires a Config submission receipt", async () => {
    const repoRoot = await testRepo();
    const configuration = testConfiguration();
    const status = await repoCommandReadinessStatus(
      repoRoot,
      configuration,
      undefined,
    );

    assert.strictEqual(status.ready, false);
    assert.match(status.reason ?? "", /Run Config: Mac Release/);
    assert.deepStrictEqual(status.missingOutputs, []);
  });

  test("accepts a matching submission and reports no missing outputs", async () => {
    const repoRoot = await testRepo();
    const configuration = testConfiguration();
    const signature = await repoCommandConfigurationSignature(
      repoRoot,
      configuration,
    );
    const status = await repoCommandReadinessStatus(repoRoot, configuration, {
      signature,
      submittedAt: new Date().toISOString(),
    });

    assert.strictEqual(status.ready, true);
    assert.deepStrictEqual(status.missingOutputs, []);
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
      submittedAt: new Date().toISOString(),
    });

    assert.strictEqual(status.ready, false);
    assert.match(status.reason ?? "", /Config inputs changed/);
  });

  test("reports missing outputs without blocking queued commands", async () => {
    const repoRoot = await testRepo();
    const configuration = testConfiguration();
    const signature = await repoCommandConfigurationSignature(
      repoRoot,
      configuration,
    );
    await fs.rm(path.join(repoRoot, "build", "release", "CMakeCache.txt"));

    const status = await repoCommandReadinessStatus(repoRoot, configuration, {
      signature,
      submittedAt: new Date().toISOString(),
    });

    assert.strictEqual(status.ready, true);
    assert.deepStrictEqual(status.missingOutputs, [
      "build/release/CMakeCache.txt",
    ]);
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
