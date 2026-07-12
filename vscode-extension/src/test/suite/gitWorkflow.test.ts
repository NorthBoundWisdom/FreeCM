import * as assert from "assert";
import { EventEmitter } from "events";
import { existsSync } from "fs";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import { PassThrough } from "stream";
import {
  ProcessLike,
  ProcessRunner,
  pullExistingSeedRepositories,
  pullWithRebaseIfClean,
} from "../../gitWorkflow";

class MockProcess extends EventEmitter implements ProcessLike {
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
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
          { repo: "AReady", args: ["pull", "--rebase"] },
          { repo: "BDirty", args: ["status", "--porcelain=v1"] },
          { repo: "CDetached", args: ["status", "--porcelain=v1"] },
          { repo: "CDetached", args: ["pull", "--rebase"] },
          { repo: "DReady", args: ["status", "--porcelain=v1"] },
          { repo: "DReady", args: ["pull", "--rebase"] },
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
});
