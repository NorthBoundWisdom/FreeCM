import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import { parse } from "jsonc-parser";
import {
  manualAll,
  pinLatest,
  readActiveLockStatus,
  readDependencyComparison,
  updateUsed,
  usePinned,
} from "../../lockWorkflow";
import { LOCK_SCHEMA_CONTRACT } from "../../lockSchema";

async function createRepoRoot(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "freecm-lock-"));
}

async function writeJsonc(
  filePath: string,
  value: Record<string, unknown>,
): Promise<void> {
  await fs.writeFile(
    filePath,
    `${JSON.stringify(coreLock(value), null, 2)}\n`,
    "utf8",
  );
}

async function readJsonc(filePath: string): Promise<Record<string, unknown>> {
  return parse(await fs.readFile(filePath, "utf8")) as Record<string, unknown>;
}

async function lockWriteArtifacts(filePath: string): Promise<string[]> {
  const directory = path.dirname(filePath);
  const baseName = path.basename(filePath);
  return (await fs.readdir(directory)).filter(
    (entry) =>
      entry === `.${baseName}.vscode.lock` ||
      (entry.startsWith(`.${baseName}.`) && entry.endsWith(".tmp")),
  );
}

async function exists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

function deps(
  value: Record<string, unknown>,
): Record<string, Record<string, unknown>> {
  return value.dependencies as Record<string, Record<string, unknown>>;
}

function coreLock(value: Record<string, unknown>): Record<string, unknown> {
  const dependencies = normalizeDependencies(
    (value.dependencies ?? {}) as Record<string, Record<string, unknown>>,
  );
  const names = Object.keys(dependencies);
  const manualPath =
    value.depsManualPath === undefined
      ? Object.fromEntries(names.map((name) => [name, ""]))
      : value.depsManualPath;
  return {
    schemaVersion: LOCK_SCHEMA_CONTRACT.schemaVersion,
    cmakeEnvironment: {},
    cmakeCacheVariables: {},
    ...value,
    dependencies,
    depsManualPath: manualPath,
  };
}

function normalizeDependencies(
  dependencies: Record<string, Record<string, unknown>>,
): Record<string, Record<string, unknown>> {
  return Object.fromEntries(
    Object.entries(dependencies).map(([name, entry]) => [
      name,
      {
        remote: `git@example.com:${name}.git`,
        ...entry,
      },
    ]),
  );
}

suite("lock workflow", () => {
  test("lock schema contract mirrors Python core constants", () => {
    assert.deepStrictEqual(LOCK_SCHEMA_CONTRACT, {
      schemaVersion: 5,
      modes: ["pinned", "latest", "manual"],
      activeLockFileName: "source_roots.lock.jsonc",
      templateLockFileName: "source_roots.lock.jsonc.in",
      workspaceLockName: ".freecm.workspace.lock",
      fields: {
        schemaVersion: "schemaVersion",
        depsMode: "depsMode",
        depsManualPath: "depsManualPath",
        dependencies: "dependencies",
        remote: "remote",
        commit: "commit",
        latestRef: "latestRef",
        repoName: "repoName",
      },
    });
  });

  test("reads dependency comparison from JSONC sample and active locks", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");

    await fs.writeFile(
      templatePath,
      `{
        // sample lock
        "schemaVersion": 5,
        "depsMode": "pinned",
        "depsManualPath": {
          "LibA": "",
          "LibB": ""
        },
        "dependencies": {
          "LibA": { "remote": "git@example.com:LibA.git", "commit": "sample-a" },
          "LibB": { "remote": "git@example.com:LibB.git", "commit": "sample-b" },
        },
      }\n`,
      "utf8",
    );
    await fs.writeFile(
      activePath,
      `{
        "schemaVersion": 5,
        "depsMode": "manual",
        "dependencies": {
          "LibB": { "remote": "git@example.com:LibB.git", "commit": "active-b" },
          "LibC": { "remote": "git@example.com:LibC.git", "commit": "active-c" },
        },
        "depsManualPath": {
          "LibB": "custom/LibB",
          "LibC": ""
        },
      }\n`,
      "utf8",
    );

    assert.deepStrictEqual(await readDependencyComparison(repoRoot), {
      sampleMode: "pinned",
      activeMode: "manual",
      rows: [
        {
          name: "LibA",
          samplePresent: true,
          sampleCommit: "sample-a",
          activePresent: false,
          activeCommit: undefined,
          activeMode: undefined,
        },
        {
          name: "LibB",
          samplePresent: true,
          sampleCommit: "sample-b",
          activePresent: true,
          activeCommit: "active-b",
          activeMode: "manual",
        },
        {
          name: "LibC",
          samplePresent: false,
          sampleCommit: undefined,
          activePresent: true,
          activeCommit: "active-c",
          activeMode: "pinned",
        },
      ],
    });
  });

  test("reads effective active mode for manual dependencies", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");

    await writeJsonc(templatePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { commit: "sample-a" },
        LibB: { commit: "sample-b" },
      },
      depsManualPath: {
        LibA: "",
        LibB: "",
      },
    });
    await writeJsonc(activePath, {
      depsMode: "manual",
      dependencies: {
        LibA: { commit: "active-a" },
        LibB: { commit: "active-b" },
      },
      depsManualPath: {
        LibA: "",
        LibB: "custom/LibB",
      },
    });

    const comparison = await readDependencyComparison(repoRoot);

    assert.deepStrictEqual(
      comparison.rows.map((row) => [
        row.name,
        row.activeMode,
        row.activeCommit,
      ]),
      [
        ["LibA", "pinned", "active-a"],
        ["LibB", "manual", "active-b"],
      ],
    );
  });

  test("reads lock state from template when active lock is absent", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");

    await writeJsonc(templatePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { commit: "sample-a" },
      },
    });

    assert.deepStrictEqual(await readActiveLockStatus(repoRoot), {
      mode: "pinned",
    });
    assert.deepStrictEqual(await readDependencyComparison(repoRoot), {
      sampleMode: "pinned",
      activeMode: "pinned",
      rows: [
        {
          name: "LibA",
          samplePresent: true,
          sampleCommit: "sample-a",
          activePresent: true,
          activeCommit: "sample-a",
          activeMode: "pinned",
        },
      ],
    });
    await assert.rejects(fs.access(activePath), /ENOENT/);
  });

  test("creates active lock from template before lock edits", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");

    await writeJsonc(templatePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "sample-a" },
      },
      depsManualPath: {
        LibA: "",
      },
    });

    await manualAll(repoRoot);

    const active = await readJsonc(activePath);
    assert.strictEqual(active.depsMode, "manual");
    assert.strictEqual(deps(active).LibA.commit, "sample-a");
    assert.deepStrictEqual(await lockWriteArtifacts(activePath), []);
  });

  test("Use pinned stops when current manual path is dirty", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");
    const manualPath = path.join(repoRoot, "custom", "LibA");
    const outputLines: Array<{ level: string; value: string }> = [];
    const checkedPaths: string[] = [];

    await writeJsonc(activePath, {
      depsMode: "manual",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "old-a" },
      },
      depsManualPath: {
        LibA: "custom/LibA",
      },
    });
    await writeJsonc(templatePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "new-a" },
      },
      depsManualPath: {
        LibA: "",
      },
    });

    await assert.rejects(
      () =>
        usePinned(repoRoot, {
          output: {
            log: (level, value) => outputLines.push({ level, value }),
          },
          dirtyChecker: async (candidatePath) => {
            checkedPaths.push(candidatePath);
            return {
              dirty: true,
              statusLines: [" M src/lib.cpp", "?? scratch.txt"],
            };
          },
        }),
      /Use pinned stopped because 1 manual dependency worktree\(s\) are dirty/,
    );

    const active = await readJsonc(activePath);
    assert.strictEqual(active.depsMode, "manual");
    assert.strictEqual(deps(active).LibA.commit, "old-a");
    assert.deepStrictEqual(checkedPaths, [manualPath]);
    assert.ok(
      outputLines.some(
        (line) =>
          line.level === "error" && line.value.includes("Refusing Use pinned"),
      ),
    );
    assert.ok(
      outputLines.some((line) => line.value.includes(" M src/lib.cpp")),
    );
  });

  test("Use pinned does not inspect manual paths when current mode is not manual", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");

    await writeJsonc(activePath, {
      depsMode: "latest",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "old-a" },
      },
      depsManualPath: {
        LibA: "custom/LibA",
      },
    });
    await writeJsonc(templatePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "new-a" },
      },
      depsManualPath: {
        LibA: "",
      },
    });

    await usePinned(repoRoot, {
      dirtyChecker: async () => {
        throw new Error("dirty checker should not run");
      },
    });

    const active = await readJsonc(activePath);
    assert.strictEqual(active.depsMode, "pinned");
    assert.strictEqual(deps(active).LibA.commit, "new-a");
  });

  test("Use pinned preserves active local config and syncs template dependencies", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");

    await writeJsonc(activePath, {
      depsMode: "manual",
      AppConfigs: { scheme: "Local" },
      cmakeCacheVariables: { FEATURE: "ON" },
      dependencies: {
        OldDep: { remote: "old", commit: "oldsha" },
      },
      depsManualPath: {
        OldDep: "build/dependency_seed_repos/OldDep",
      },
    });
    await writeJsonc(templatePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "aaa111" },
        LibB: { remote: "git@example.com:LibB.git", commit: "bbb222" },
      },
      depsManualPath: {
        LibA: "",
        LibB: "",
      },
    });

    await usePinned(repoRoot);

    const active = await readJsonc(activePath);
    assert.strictEqual(active.depsMode, "pinned");
    assert.deepStrictEqual(deps(active), {
      LibA: { remote: "git@example.com:LibA.git", commit: "aaa111" },
      LibB: { remote: "git@example.com:LibB.git", commit: "bbb222" },
    });
    assert.deepStrictEqual(active.depsManualPath, {
      LibA: "",
      LibB: "",
    });
    assert.deepStrictEqual(active.AppConfigs, { scheme: "Local" });
    assert.deepStrictEqual(active.cmakeCacheVariables, { FEATURE: "ON" });
  });

  test("Use pinned accepts core-ignored legacy dependency fields without preserving them", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");

    await writeJsonc(activePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { commit: "old-a" },
      },
    });
    await writeJsonc(templatePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { commit: "new-a", abiGroup: "legacy" },
      },
    });

    await usePinned(repoRoot);

    const active = await readJsonc(activePath);
    assert.deepStrictEqual(deps(active).LibA, {
      remote: "git@example.com:LibA.git",
      commit: "new-a",
    });
  });

  test("lock edits reject fields Python core would reject", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");

    await writeJsonc(activePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { commit: "old-a", repoName: "../RepoA" },
      },
    });
    await assert.rejects(() => manualAll(repoRoot), /Invalid repository name/);

    await writeJsonc(activePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { commit: "old-a", latestRef: 123 },
      },
    });
    await assert.rejects(() => manualAll(repoRoot), /Invalid field latestRef/);
  });

  test("Manual all writes relative dependency seed paths", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");

    await writeJsonc(activePath, {
      depsMode: "latest",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "aaa111" },
        LibB: { remote: "git@example.com:LibB.git", commit: "bbb222" },
      },
      depsManualPath: {
        LibA: "",
        LibB: "",
      },
    });

    await manualAll(repoRoot);

    const active = await readJsonc(activePath);
    assert.strictEqual(active.depsMode, "manual");
    assert.deepStrictEqual(active.depsManualPath, {
      LibA: "build/dependency_seed_repos/LibA",
      LibB: "build/dependency_seed_repos/LibB",
    });
  });

  test("Manual all stops before replacing dirty custom manual paths", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const outputLines: Array<{ level: string; value: string }> = [];

    await writeJsonc(activePath, {
      depsMode: "manual",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "aaa111" },
      },
      depsManualPath: {
        LibA: "/tmp/custom-LibA",
      },
    });

    await assert.rejects(
      () =>
        manualAll(repoRoot, {
          output: {
            log: (level, value) => outputLines.push({ level, value }),
          },
          dirtyChecker: async (candidatePath) => {
            assert.strictEqual(candidatePath, "/tmp/custom-LibA");
            return { dirty: true, statusLines: ["M  README.md"] };
          },
        }),
      /Manual all stopped because 1 manual dependency worktree\(s\) are dirty/,
    );

    const active = await readJsonc(activePath);
    assert.strictEqual(active.depsMode, "manual");
    assert.deepStrictEqual(active.depsManualPath, {
      LibA: "/tmp/custom-LibA",
    });
    assert.ok(
      outputLines.some((line) => line.value.includes("Refusing Manual all")),
    );
  });

  test("Manual all does not inspect paths when current mode is not manual", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");

    await writeJsonc(activePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "aaa111" },
      },
      depsManualPath: {
        LibA: "custom/LibA",
      },
    });

    await manualAll(repoRoot, {
      dirtyChecker: async () => {
        throw new Error("dirty checker should not run");
      },
    });

    const active = await readJsonc(activePath);
    assert.strictEqual(active.depsMode, "manual");
    assert.deepStrictEqual(active.depsManualPath, {
      LibA: "build/dependency_seed_repos/LibA",
    });
  });

  test("Pin latest runs update once and pins active lock to latest local commits", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");
    let updateCalls = 0;

    await writeJsonc(activePath, {
      depsMode: "pinned",
      App: { bundle: "local" },
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "old-a" },
        LibB: { remote: "git@example.com:LibB.git", commit: "old-b" },
      },
      depsManualPath: {
        LibA: "",
        LibB: "",
      },
    });
    await writeJsonc(templatePath, {
      depsMode: "latest",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "template-a" },
        LibB: { remote: "git@example.com:LibB.git", commit: "template-b" },
      },
      depsManualPath: {
        LibA: "old",
        LibB: "old",
      },
    });

    await pinLatest(repoRoot, async (cwd) => {
      updateCalls += 1;
      assert.strictEqual(cwd, repoRoot);
      const activeBeforeUpdate = await readJsonc(activePath);
      assert.strictEqual(activeBeforeUpdate.depsMode, "latest");
      const workspaceLockPath = path.join(repoRoot, ".freecm.workspace.lock");
      await fs.mkdir(workspaceLockPath);
      await fs.rm(workspaceLockPath, { recursive: true, force: true });
      await writeJsonc(activePath, {
        ...activeBeforeUpdate,
        dependencies: {
          LibA: { remote: "git@example.com:LibA.git", commit: "new-a" },
          LibB: { remote: "git@example.com:LibB.git", commit: "new-b" },
        },
      });
    });

    const active = await readJsonc(activePath);
    const template = await readJsonc(templatePath);
    assert.strictEqual(updateCalls, 1);
    assert.strictEqual(active.depsMode, "pinned");
    assert.deepStrictEqual(active.App, { bundle: "local" });
    assert.strictEqual(deps(active).LibA.commit, "new-a");
    assert.strictEqual(deps(active).LibB.commit, "new-b");
    assert.deepStrictEqual(active.depsManualPath, {
      LibA: "",
      LibB: "",
    });
    assert.strictEqual(deps(template).LibA.commit, "template-a");
    assert.strictEqual(deps(template).LibB.commit, "template-b");
  });

  test("Pin latest checks dirty manual paths before changing mode", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    let updateCalls = 0;

    await writeJsonc(activePath, {
      depsMode: "manual",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "old-a" },
      },
      depsManualPath: {
        LibA: "custom/LibA",
      },
    });

    await assert.rejects(
      () =>
        pinLatest(
          repoRoot,
          async () => {
            updateCalls += 1;
          },
          {
            dirtyChecker: async () => ({
              dirty: true,
              statusLines: [" M local.cpp"],
            }),
          },
        ),
      /Pin latest stopped because 1 manual dependency worktree\(s\) are dirty/,
    );

    const active = await readJsonc(activePath);
    assert.strictEqual(active.depsMode, "manual");
    assert.strictEqual(updateCalls, 0);
  });

  test("Pin latest restores active lock when update fails", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const original = {
      depsMode: "pinned",
      App: { bundle: "local" },
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "old-a" },
      },
      depsManualPath: {
        LibA: "",
      },
    };
    await writeJsonc(activePath, original);
    const expectedOriginal = await readJsonc(activePath);

    await assert.rejects(
      () =>
        pinLatest(repoRoot, async () => {
          const workspaceLockPath = path.join(repoRoot, ".freecm.workspace.lock");
          await fs.mkdir(workspaceLockPath);
          await fs.rm(workspaceLockPath, { recursive: true, force: true });
          throw new Error("update failed");
        }),
      /update failed/,
    );

    assert.deepStrictEqual(await readJsonc(activePath), expectedOriginal);
  });

  test("lock mode operations release the workspace mutex while update runs", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    let updateCalls = 0;
    let signalUpdateStarted!: () => void;
    const updateStarted = new Promise<void>((resolve) => {
      signalUpdateStarted = resolve;
    });

    await writeJsonc(activePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "old-a" },
      },
      depsManualPath: {
        LibA: "",
      },
    });

    const pinLatestTask = pinLatest(repoRoot, async () => {
      updateCalls += 1;
      const activeBeforeUpdate = await readJsonc(activePath);
      assert.strictEqual(activeBeforeUpdate.depsMode, "latest");
      assert.strictEqual(
        await exists(path.join(repoRoot, ".freecm.workspace.lock")),
        false,
      );
      signalUpdateStarted();
      await new Promise((resolve) => setTimeout(resolve, 25));
      await writeJsonc(activePath, {
        ...activeBeforeUpdate,
        dependencies: {
          LibA: { remote: "git@example.com:LibA.git", commit: "new-a" },
        },
      });
    });
    await updateStarted;
    const manualAllTask = manualAll(repoRoot);
    await Promise.all([pinLatestTask, manualAllTask]);

    const active = await readJsonc(activePath);
    assert.strictEqual(updateCalls, 1);
    assert.strictEqual(active.depsMode, "pinned");
    assert.strictEqual(deps(active).LibA.commit, "new-a");
    assert.strictEqual(
      await exists(path.join(repoRoot, ".freecm.workspace.lock")),
      false,
    );
  });

  test("Update used copies active pinned commits to template lock", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");

    await writeJsonc(activePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "used-a" },
        LibB: { remote: "git@example.com:LibB.git", commit: "used-b" },
      },
      depsManualPath: {
        LibA: "",
        LibB: "",
      },
    });
    await writeJsonc(templatePath, {
      depsMode: "latest",
      dependencies: {
        LibA: { remote: "git@example.com:LibA.git", commit: "template-a" },
        LibB: { remote: "git@example.com:LibB.git", commit: "template-b" },
      },
      depsManualPath: {
        LibA: "old",
        LibB: "old",
      },
    });

    const result = await updateUsed(repoRoot);

    const template = await readJsonc(templatePath);
    assert.deepStrictEqual(result.updatedDependencies, ["LibA", "LibB"]);
    assert.strictEqual(template.depsMode, "pinned");
    assert.strictEqual(deps(template).LibA.commit, "used-a");
    assert.strictEqual(deps(template).LibB.commit, "used-b");
    assert.deepStrictEqual(template.depsManualPath, {
      LibA: "",
      LibB: "",
    });
  });

  test("Update used accepts active latest mode and rejects manual mode", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");

    await writeJsonc(activePath, {
      depsMode: "latest",
      dependencies: {
        LibA: { commit: "used-a" },
      },
      depsManualPath: {
        LibA: "",
      },
    });
    await writeJsonc(templatePath, {
      depsMode: "pinned",
      dependencies: {
        LibA: { commit: "template-a" },
      },
      depsManualPath: {
        LibA: "",
      },
    });

    await updateUsed(repoRoot);
    assert.strictEqual(
      deps(await readJsonc(templatePath)).LibA.commit,
      "used-a",
    );

    await writeJsonc(activePath, {
      depsMode: "manual",
      dependencies: {
        LibA: { commit: "manual-a" },
      },
      depsManualPath: {
        LibA: "custom/LibA",
      },
    });

    await assert.rejects(
      () => updateUsed(repoRoot),
      /Update used requires active lock depsMode to be pinned or latest/,
    );
  });

  test("JSONC comments do not prevent lock edits", async () => {
    const repoRoot = await createRepoRoot();
    const activePath = path.join(repoRoot, "source_roots.lock.jsonc");
    const templatePath = path.join(repoRoot, "source_roots.lock.jsonc.in");

    await fs.writeFile(
      activePath,
      `{
        // local mode
        "schemaVersion": 5,
        "depsMode": "latest",
        "dependencies": {
          "LibA": { "remote": "git@example.com:LibA.git", "commit": "old-a" }
        },
        "depsManualPath": { "LibA": "" }
      }\n`,
      "utf8",
    );
    await fs.writeFile(
      templatePath,
      `{
        // pinned template
        "schemaVersion": 5,
        "depsMode": "pinned",
        "dependencies": {
          "LibA": { "remote": "git@example.com:LibA.git", "commit": "template-a" }
        },
        "depsManualPath": { "LibA": "" }
      }\n`,
      "utf8",
    );

    await usePinned(repoRoot);

    const active = await readJsonc(activePath);
    assert.strictEqual(active.depsMode, "pinned");
    assert.strictEqual(deps(active).LibA.commit, "template-a");
  });
});
