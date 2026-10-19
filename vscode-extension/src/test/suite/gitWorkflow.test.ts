import * as assert from "assert";
import { execFile } from "child_process";
import { EventEmitter } from "events";
import { existsSync } from "fs";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import { PassThrough } from "stream";
import { promisify } from "util";
import {
  nodeProcessRunner,
  ProcessLike,
  ProcessRunner,
  pullExistingSeedRepositories,
  pullWithRebaseIfClean,
} from "../../gitWorkflow";
import {
  ACTIVE_LOCK_NAME,
  LOCK_SCHEMA_VERSION,
  TEMPLATE_LOCK_NAME,
} from "../../lockSchema";

class MockProcess extends EventEmitter implements ProcessLike {
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
}

async function writeSeedTemplate(
  repoRoot: string,
  names: readonly string[],
  disabledNames: readonly string[] = [],
): Promise<void> {
  await fs.writeFile(
    path.join(repoRoot, TEMPLATE_LOCK_NAME),
    `// Repositories eligible for Pull Seeds.\n${JSON.stringify({
      schemaVersion: LOCK_SCHEMA_VERSION,
      depsMode: "pinned",
      depsManualPath: Object.fromEntries(names.map((name) => [name, ""])),
      dependencies: Object.fromEntries(names.map((name) => [
        name,
        disabledNames.includes(name)
          ? { disabled: true }
          : { remote: `https://example.invalid/${name}.git`, commit: "a".repeat(40) },
      ])),
    }, null, 2)}\n`,
    "utf8",
  );
}

const execFileAsync = promisify(execFile);

async function fixtureGit(cwd: string, ...args: string[]): Promise<string> {
  const result = await execFileAsync(
    "git",
    ["-c", "protocol.file.allow=always", ...args],
    {
      cwd,
      encoding: "utf8",
      env: {
        ...process.env,
        GIT_AUTHOR_NAME: "FreeCM",
        GIT_AUTHOR_EMAIL: "freecm@example.invalid",
        GIT_COMMITTER_NAME: "FreeCM",
        GIT_COMMITTER_EMAIL: "freecm@example.invalid",
      },
    },
  );
  return result.stdout.trim();
}

async function commitFixture(cwd: string, message: string): Promise<string> {
  await fixtureGit(cwd, "add", ".");
  await fixtureGit(cwd, "commit", "-m", message);
  return fixtureGit(cwd, "rev-parse", "HEAD");
}

suite("git workflow", () => {
  test("pulls with rebase when worktree is clean", async () => {
    const calls: Array<{
      command: string;
      args: readonly string[];
      cwd: string;
    }> = [];
    const logs: Array<{ level: string; value: string }> = [];
    const runner: ProcessRunner = {
      spawn(command, args, options) {
        calls.push({ command, args, cwd: options.cwd });
        const child = new MockProcess();
        queueMicrotask(() => {
          if (
            args[0] === "symbolic-ref" &&
            args[1] === "-q" &&
            args[2] === "--short"
          ) {
            child.stdout.write("master\n");
            child.emit("close", 0, null);
            return;
          }
          if (args[0] === "pull") {
            child.stdout.write("Already up to date.\n");
          }
          child.emit("close", 0, null);
        });
        return child;
      },
    };

    await pullWithRebaseIfClean(
      "/repo/Host",
      "Host",
      { log: (level, value) => logs.push({ level, value }) },
      runner,
    );

    assert.deepStrictEqual(calls, [
      {
        command: "git",
        args: ["status", "--porcelain=v1"],
        cwd: "/repo/Host",
      },
      {
        command: "git",
        args: ["symbolic-ref", "-q", "--short", "HEAD"],
        cwd: "/repo/Host",
      },
      {
        command: "git",
        args: ["pull", "--rebase"],
        cwd: "/repo/Host",
      },
    ]);
    assert.ok(logs.some((log) => log.value === "Already up to date."));
    assert.ok(logs.some((log) => log.level === "success"));
  });

  test("refreshes detached head from tracked remote branch when detached", async () => {
    const calls: Array<{
      command: string;
      args: readonly string[];
      cwd: string;
    }> = [];
    const logs: Array<{ level: string; value: string }> = [];
    const runner: ProcessRunner = {
      spawn(command, args, options) {
        calls.push({ command, args, cwd: options.cwd });
        const child = new MockProcess();
        queueMicrotask(() => {
          if (
            args[0] === "symbolic-ref" &&
            args[1] === "-q" &&
            args[2] === "--short"
          ) {
            child.emit("close", 1, null);
            return;
          }
          if (
            args[0] === "symbolic-ref" &&
            args[1] === "-q" &&
            args[2] === "refs/remotes/origin/HEAD"
          ) {
            child.stdout.write("refs/remotes/origin/master\n");
            child.emit("close", 0, null);
            return;
          }
          if (args[0] === "fetch") {
            child.stdout.write(
              "From github.com:NorthBoundWisdom/RepoConfigsMgr\n",
            );
            child.emit("close", 0, null);
            return;
          }
          if (args[0] === "reset") {
            child.stdout.write(
              "HEAD is now at afac67d fix: handle detached head pull branches\n",
            );
          }
          child.emit("close", 0, null);
        });
        return child;
      },
    };

    await pullWithRebaseIfClean(
      "/repo/FreeCM",
      "FreeCM",
      { log: (level, value) => logs.push({ level, value }) },
      runner,
    );

    assert.deepStrictEqual(
      calls.map((call) => call.args),
      [
        ["status", "--porcelain=v1"],
        ["symbolic-ref", "-q", "--short", "HEAD"],
        ["symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
        ["fetch", "origin", "master"],
        ["reset", "--hard", "origin/master"],
      ],
    );
    assert.ok(
      logs.some((log) =>
        log.value.includes(
          "Detached HEAD; refreshing FreeCM from origin/master.",
        ),
      ),
    );
  });

  test("reports dirty worktree and skips pull", async () => {
    const calls: Array<{ args: readonly string[] }> = [];
    const logs: Array<{ level: string; value: string }> = [];
    const runner: ProcessRunner = {
      spawn(_command, args) {
        calls.push({ args });
        const child = new MockProcess();
        queueMicrotask(() => {
          child.stdout.write(" M README.md\n?? scratch.txt\n");
          child.emit("close", 0, null);
        });
        return child;
      },
    };

    await assert.rejects(
      () =>
        pullWithRebaseIfClean(
          "/repo/Host",
          "Host",
          { log: (level, value) => logs.push({ level, value }) },
          runner,
        ),
      /Host worktree is dirty/,
    );

    assert.deepStrictEqual(calls, [{ args: ["status", "--porcelain=v1"] }]);
    assert.ok(
      logs.some((log) => log.level === "error" && log.value.includes("dirty")),
    );
    assert.ok(logs.some((log) => log.value.includes(" M README.md")));
  });

  test("does nothing when dependency seed repositories do not exist", async () => {
    const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-pull-seeds-empty-"));
    const logs: Array<{ level: string; value: string }> = [];
    let spawnCount = 0;
    try {
      await writeSeedTemplate(repoRoot, ["LibA"]);
      const summary = await pullExistingSeedRepositories(
        repoRoot,
        { log: (level, value) => logs.push({ level, value }) },
        {
          spawn() {
            spawnCount += 1;
            return new MockProcess();
          },
        },
      );

      assert.deepStrictEqual(summary, {
        succeeded: [],
        skipped: [],
        failed: [],
      });
      assert.strictEqual(spawnCount, 0);
      assert.ok(logs.some((log) => log.value.includes("No existing dependency seed")));
      assert.strictEqual(existsSync(path.join(repoRoot, ".freecm.workspace.lock")), false);
    } finally {
      await fs.rm(repoRoot, { recursive: true, force: true });
    }
  });

  test("pulls clean Git seeds in order and continues past skips and failures", async () => {
    const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-pull-seeds-"));
    const seedRoot = path.join(repoRoot, "build", "dependency_seed_repos");
    const seedNames = ["DReady", "BDirty", "CDetached", "AReady"];
    const calls: Array<{ repo: string; args: readonly string[]; lockHeld: boolean }> = [];
    const logs: Array<{ level: string; value: string }> = [];
    try {
      await writeSeedTemplate(repoRoot, [...seedNames, "AssetBundle", "SymlinkMarker"]);
      await Promise.all(
        seedNames.map((name) =>
          fs.mkdir(path.join(seedRoot, name), { recursive: true }),
        ),
      );
      await Promise.all([
        fs.mkdir(path.join(seedRoot, "AReady", ".git")),
        fs.mkdir(path.join(seedRoot, "BDirty", ".git")),
        fs.writeFile(path.join(seedRoot, "CDetached", ".git"), "gitdir: elsewhere\n"),
        fs.mkdir(path.join(seedRoot, "DReady", ".git")),
        fs.mkdir(path.join(seedRoot, "AssetBundle"), { recursive: true }),
      ]);
      if (process.platform !== "win32") {
        await fs.mkdir(path.join(seedRoot, "SymlinkMarker"), { recursive: true });
        await fs.symlink(
          path.join(seedRoot, "AReady", ".git"),
          path.join(seedRoot, "SymlinkMarker", ".git"),
        );
      }

      const runner: ProcessRunner = {
        spawn(_command, args, options) {
          const repo = path.basename(options.cwd);
          calls.push({
            repo,
            args,
            lockHeld: existsSync(path.join(repoRoot, ".freecm.workspace.lock")),
          });
          const child = new MockProcess();
          queueMicrotask(() => {
            if (args[0] === "status" && repo === "BDirty") {
              child.stdout.write(" M local.txt\n");
            } else if (args[0] === "pull" && repo === "CDetached") {
              child.stderr.write("You are not currently on a branch.\n");
              child.emit("close", 1, null);
              return;
            } else if (args[0] === "pull") {
              child.stdout.write("Already up to date.\n");
            }
            child.emit("close", 0, null);
          });
          return child;
        },
      };

      const summary = await pullExistingSeedRepositories(
        repoRoot,
        { log: (level, value) => logs.push({ level, value }) },
        runner,
      );

      assert.deepStrictEqual(summary, {
        succeeded: ["AReady", "DReady"],
        skipped: ["BDirty"],
        failed: ["CDetached"],
      });
      assert.deepStrictEqual(
        calls.map(({ repo, args }) => ({ repo, args })),
        [
          { repo: "AReady", args: ["status", "--porcelain=v1"] },
          { repo: "AReady", args: ["pull", "--rebase", "--recurse-submodules"] },
          { repo: "BDirty", args: ["status", "--porcelain=v1"] },
          { repo: "CDetached", args: ["status", "--porcelain=v1"] },
          { repo: "CDetached", args: ["pull", "--rebase", "--recurse-submodules"] },
          { repo: "DReady", args: ["status", "--porcelain=v1"] },
          { repo: "DReady", args: ["pull", "--rebase", "--recurse-submodules"] },
        ],
      );
      assert.ok(calls.every((call) => call.lockHeld));
      assert.ok(!calls.some((call) => call.args[0] === "fetch" || call.args[0] === "reset"));
      assert.ok(
        logs.some((log) =>
          log.value.includes("Pull Seeds summary: 2 succeeded, 1 skipped, 1 failed"),
        ),
      );
      assert.ok(logs.some((log) => log.value === "Succeeded: AReady, DReady"));
      assert.ok(logs.some((log) => log.value === "Skipped: BDirty"));
      assert.ok(logs.some((log) => log.value === "Failed: CDetached"));
      assert.strictEqual(existsSync(path.join(repoRoot, ".freecm.workspace.lock")), false);
    } finally {
      await fs.rm(repoRoot, { recursive: true, force: true });
    }
  });

  test("pulls template dependencies including disabled entries and ignores obsolete seeds", async () => {
    const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-pull-seeds-template-"));
    const seedRoot = path.join(repoRoot, "build", "dependency_seed_repos");
    const calls: Array<{ repo: string; args: readonly string[] }> = [];
    try {
      await writeSeedTemplate(repoRoot, ["ActiveOnly", "Obsolete"]);
      const activePath = path.join(repoRoot, ACTIVE_LOCK_NAME);
      await fs.copyFile(path.join(repoRoot, TEMPLATE_LOCK_NAME), activePath);
      await writeSeedTemplate(repoRoot, ["LibA", "LibB", "Missing"], ["LibB"]);
      const templatePath = path.join(repoRoot, TEMPLATE_LOCK_NAME);
      const beforeTemplate = await fs.readFile(templatePath, "utf8");
      const beforeActive = await fs.readFile(activePath, "utf8");
      for (const name of ["LibA", "LibB", "Obsolete", "ActiveOnly"]) {
        await fs.mkdir(path.join(seedRoot, name, ".git"), { recursive: true });
      }
      const summary = await pullExistingSeedRepositories(
        repoRoot,
        { log() {} },
        {
          spawn(_command, args, options) {
            calls.push({ repo: path.basename(options.cwd), args });
            const child = new MockProcess();
            queueMicrotask(() => child.emit("close", 0, null));
            return child;
          },
        },
      );

      assert.deepStrictEqual(summary, {
        succeeded: ["LibA", "LibB"], skipped: [], failed: [],
      });
      assert.deepStrictEqual(calls, [
        { repo: "LibA", args: ["status", "--porcelain=v1"] },
        { repo: "LibA", args: ["pull", "--rebase", "--recurse-submodules"] },
        { repo: "LibB", args: ["status", "--porcelain=v1"] },
        { repo: "LibB", args: ["pull", "--rebase", "--recurse-submodules"] },
      ]);
      assert.strictEqual(existsSync(path.join(seedRoot, "Missing")), false);
      assert.strictEqual(await fs.readFile(templatePath, "utf8"), beforeTemplate);
      assert.strictEqual(await fs.readFile(activePath, "utf8"), beforeActive);
    } finally {
      await fs.rm(repoRoot, { recursive: true, force: true });
    }
  });

  test("does not access Git when the template is missing or invalid", async () => {
    const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-pull-seeds-invalid-"));
    let spawnCount = 0;
    const runner: ProcessRunner = {
      spawn() {
        spawnCount += 1;
        return new MockProcess();
      },
    };
    try {
      await fs.mkdir(path.join(repoRoot, "build", "dependency_seed_repos", "LibA", ".git"), {
        recursive: true,
      });
      await writeSeedTemplate(repoRoot, ["LibA"]);
      const templatePath = path.join(repoRoot, TEMPLATE_LOCK_NAME);
      await fs.rename(templatePath, path.join(repoRoot, ACTIVE_LOCK_NAME));
      for (const invalidText of [undefined, "{ invalid", "{}"] as const) {
        if (invalidText !== undefined) {
          await fs.writeFile(templatePath, invalidText, "utf8");
        }
        await assert.rejects(
          pullExistingSeedRepositories(repoRoot, { log() {} }, runner),
          /source_roots\.lock\.jsonc\.in/,
        );
        assert.strictEqual(existsSync(path.join(repoRoot, ".freecm.workspace.lock")), false);
      }
      assert.strictEqual(spawnCount, 0);
    } finally {
      await fs.rm(repoRoot, { recursive: true, force: true });
    }
  });

  test("keeps populated nested submodules aligned after their upstream gitlinks change", async function () {
    this.timeout(30_000);
    const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-pull-seeds-submodules-"));
    const remotesRoot = path.join(repoRoot, "remotes");
    const libA = path.join(remotesRoot, "LibA");
    const libB = path.join(remotesRoot, "LibB");
    const module = path.join(remotesRoot, "Module");
    const seed = path.join(repoRoot, "build", "dependency_seed_repos", "LibA");
    try {
      for (const directory of [module, libB, libA]) {
        await fs.mkdir(directory, { recursive: true });
        await fixtureGit(directory, "init");
        await fs.writeFile(path.join(directory, "source.txt"), "initial\n");
        await commitFixture(directory, "initial");
      }
      await fixtureGit(libB, "submodule", "add", module, "module");
      await commitFixture(libB, "add nested module");
      await fixtureGit(libA, "submodule", "add", libB, "libB");
      await commitFixture(libA, "add child module");
      await fs.mkdir(path.dirname(seed), { recursive: true });
      await fixtureGit(repoRoot, "clone", "--recurse-submodules", libA, seed);
      await writeSeedTemplate(repoRoot, ["LibA"]);

      await fs.writeFile(path.join(module, "source.txt"), "updated\n");
      const moduleCommit = await commitFixture(module, "advance nested module");
      await fixtureGit(path.join(libB, "module"), "fetch", "origin");
      await fixtureGit(path.join(libB, "module"), "checkout", moduleCommit);
      const libBCommit = await commitFixture(libB, "advance nested gitlink");
      await fixtureGit(path.join(libA, "libB"), "fetch", "origin");
      await fixtureGit(path.join(libA, "libB"), "checkout", libBCommit);
      const libACommit = await commitFixture(libA, "advance child gitlink");

      const summary = await pullExistingSeedRepositories(
        repoRoot,
        { log() {} },
        {
          spawn(command, args, options) {
            return nodeProcessRunner.spawn(
              command, ["-c", "protocol.file.allow=always", ...args], options,
            );
          },
        },
      );

      assert.deepStrictEqual(summary, { succeeded: ["LibA"], skipped: [], failed: [] });
      assert.strictEqual(await fixtureGit(seed, "rev-parse", "HEAD"), libACommit);
      assert.strictEqual(await fixtureGit(path.join(seed, "libB"), "rev-parse", "HEAD"), libBCommit);
      assert.strictEqual(
        await fixtureGit(path.join(seed, "libB", "module"), "rev-parse", "HEAD"), moduleCommit,
      );
      assert.strictEqual(await fixtureGit(seed, "status", "--porcelain=v1"), "");
      assert.strictEqual(await fixtureGit(path.join(seed, "libB"), "status", "--porcelain=v1"), "");
    } finally {
      await fs.rm(repoRoot, { recursive: true, force: true });
    }
  });
});
