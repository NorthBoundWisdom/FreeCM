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
    const calls: Array<{ command: string; args: readonly string[]; cwd: string }> = [];
    const logs: Array<{ level: string; value: string }> = [];
    const runner: ProcessRunner = {
      spawn(command, args, options) {
        calls.push({ command, args, cwd: options.cwd });
        const child = new MockProcess();
        queueMicrotask(() => {
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
        args: ["pull", "--rebase"],
        cwd: "/repo/Host",
      },
    ]);
    assert.ok(logs.some((log) => log.value === "Already up to date."));
    assert.ok(logs.some((log) => log.level === "success"));
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
    assert.ok(logs.some((log) => log.level === "error" && log.value.includes("dirty")));
    assert.ok(logs.some((log) => log.value.includes(" M README.md")));
  });
});
