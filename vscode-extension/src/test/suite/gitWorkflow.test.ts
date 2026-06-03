import * as assert from "assert";
import { EventEmitter } from "events";
import { PassThrough } from "stream";
import {
  ProcessLike,
  ProcessRunner,
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
});
