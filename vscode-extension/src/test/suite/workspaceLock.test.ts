import * as assert from "assert";
import {
  ChildProcessWithoutNullStreams,
  spawn,
} from "child_process";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import { WORKSPACE_LOCK_NAME, WORKSPACE_LOCK_PROTOCOL } from "../../lockSchema";
import { withWorkspaceLock } from "../../workspaceLock";

const FREECM_ROOT = path.resolve(__dirname, "../../../..");

async function createRepoRoot(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "freecm-workspace-lock-"));
}

function lockPath(repoRoot: string): string {
  return path.join(repoRoot, WORKSPACE_LOCK_NAME);
}

function ownerPath(repoRoot: string): string {
  return path.join(
    lockPath(repoRoot),
    WORKSPACE_LOCK_PROTOCOL.ownerFileName,
  );
}

function ownerData(
  token: string,
  pid: number,
  implementation = "vscode",
): Record<string, unknown> {
  return {
    schemaVersion: WORKSPACE_LOCK_PROTOCOL.schemaVersion,
    token,
    pid,
    processStartToken: null,
    hostname: os.hostname().trim().toLowerCase(),
    implementation,
    acquiredAt: new Date().toISOString(),
  };
}

function pythonArgs(script: string, args: readonly string[]): {
  readonly command: string;
  readonly args: string[];
} {
  const configuredCommand = process.env.FREECM_TEST_PYTHON?.trim();
  if (configuredCommand !== undefined && configuredCommand.length > 0) {
    return { command: configuredCommand, args: ["-c", script, ...args] };
  }
  return process.platform === "win32"
    ? { command: "py", args: ["-3", "-c", script, ...args] }
    : { command: "python3", args: ["-c", script, ...args] };
}

function pythonEnvironment(): NodeJS.ProcessEnv {
  const existing = process.env.PYTHONPATH;
  return {
    ...process.env,
    PYTHONPATH:
      existing === undefined || existing.length === 0
        ? FREECM_ROOT
        : `${FREECM_ROOT}${path.delimiter}${existing}`,
  };
}

function spawnPython(
  script: string,
  args: readonly string[],
): ChildProcessWithoutNullStreams {
  const invocation = pythonArgs(script, args);
  return spawn(invocation.command, invocation.args, {
    cwd: FREECM_ROOT,
    env: pythonEnvironment(),
    shell: false,
    stdio: "pipe",
    windowsHide: true,
  });
}

async function runPython(
  script: string,
  args: readonly string[],
): Promise<{ readonly exitCode: number | null; readonly stdout: string; readonly stderr: string }> {
  const child = spawnPython(script, args);
  return collectProcess(child);
}

async function collectProcess(
  child: ChildProcessWithoutNullStreams,
): Promise<{ readonly exitCode: number | null; readonly stdout: string; readonly stderr: string }> {
  return new Promise((resolve, reject) => {
    let stdout = "";
    let stderr = "";
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error("Child process timed out"));
    }, 10000);
    child.stdout.on("data", (chunk: Buffer | string) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer | string) => {
      stderr += chunk.toString();
    });
    child.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.on("close", (exitCode) => {
      clearTimeout(timeout);
      resolve({ exitCode, stdout, stderr });
    });
  });
}

async function waitForOutput(
  child: ChildProcessWithoutNullStreams,
  expected: string,
): Promise<void> {
  return new Promise((resolve, reject) => {
    let stdout = "";
    let stderr = "";
    const timeout = setTimeout(() => {
      cleanup();
      reject(
        new Error(
          `Timed out waiting for ${JSON.stringify(expected)}; stdout=${JSON.stringify(stdout)} stderr=${JSON.stringify(stderr)}`,
        ),
      );
    }, 10000);
    const onStdout = (chunk: Buffer | string): void => {
      stdout += chunk.toString();
      if (stdout.includes(expected)) {
        cleanup();
        resolve();
      }
    };
    const onStderr = (chunk: Buffer | string): void => {
      stderr += chunk.toString();
    };
    const onExit = (exitCode: number | null): void => {
      cleanup();
      reject(
        new Error(
          `Child exited with ${exitCode}; stdout=${JSON.stringify(stdout)} stderr=${JSON.stringify(stderr)}`,
        ),
      );
    };
    const cleanup = (): void => {
      clearTimeout(timeout);
      child.stdout.off("data", onStdout);
      child.stderr.off("data", onStderr);
      child.off("close", onExit);
    };
    child.stdout.on("data", onStdout);
    child.stderr.on("data", onStderr);
    child.on("close", onExit);
  });
}

async function waitForExit(child: ChildProcessWithoutNullStreams): Promise<number | null> {
  if (child.exitCode !== null) {
    return child.exitCode;
  }
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error("Child process did not exit"));
    }, 10000);
    child.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.on("close", (exitCode) => {
      clearTimeout(timeout);
      resolve(exitCode);
    });
  });
}

async function exists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

suite("workspace lock", () => {
  test("supports nested locks in one async call chain", async () => {
    const repoRoot = await createRepoRoot();

    await withWorkspaceLock(repoRoot, async () => {
      const outerOwner = JSON.parse(await fs.readFile(ownerPath(repoRoot), "utf8")) as {
        token: string;
      };
      await withWorkspaceLock(
        repoRoot,
        async () => {
          const innerOwner = JSON.parse(
            await fs.readFile(ownerPath(repoRoot), "utf8"),
          ) as { token: string };
          assert.strictEqual(innerOwner.token, outerOwner.token);
        },
        { timeoutMs: 10 },
      );
      assert.strictEqual(await exists(lockPath(repoRoot)), true);
    });

    assert.strictEqual(await exists(lockPath(repoRoot)), false);
  });

  test("resolves symlinked workspace roots to one lock", async function () {
    const repoRoot = await createRepoRoot();
    const aliasRoot = `${repoRoot}-alias`;
    try {
      await fs.symlink(
        repoRoot,
        aliasRoot,
        process.platform === "win32" ? "junction" : "dir",
      );
    } catch {
      this.skip();
      return;
    }

    try {
      await withWorkspaceLock(repoRoot, async () => {
        const outerOwner = await fs.readFile(ownerPath(repoRoot), "utf8");
        await withWorkspaceLock(aliasRoot, async () => {
          assert.strictEqual(
            await fs.readFile(ownerPath(repoRoot), "utf8"),
            outerOwner,
          );
        });
      });
    } finally {
      await fs.unlink(aliasRoot);
    }
  });

  test("serializes independent top-level async operations", async () => {
    const repoRoot = await createRepoRoot();
    const order: string[] = [];
    let releaseFirst: (() => void) | undefined;
    let markEntered: (() => void) | undefined;
    const entered = new Promise<void>((resolve) => {
      markEntered = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });

    const first = withWorkspaceLock(repoRoot, async () => {
      order.push("first:start");
      markEntered?.();
      await gate;
      order.push("first:end");
    });
    await entered;
    const second = withWorkspaceLock(
      repoRoot,
      async () => {
        order.push("second");
      },
      { timeoutMs: 1000, retryDelayMs: 5 },
    );
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.deepStrictEqual(order, ["first:start"]);
    releaseFirst?.();
    await Promise.all([first, second]);

    assert.deepStrictEqual(order, ["first:start", "first:end", "second"]);
  });

  test("serializes sibling locks inherited from an outer context", async () => {
    const outerRoot = await createRepoRoot();
    const siblingRoot = await createRepoRoot();
    const events: string[] = [];
    let releaseFirst: (() => void) | undefined;
    let markEntered: (() => void) | undefined;
    const entered = new Promise<void>((resolve) => {
      markEntered = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });

    await withWorkspaceLock(outerRoot, async () => {
      const first = withWorkspaceLock(siblingRoot, async () => {
        events.push("first:start");
        markEntered?.();
        await gate;
        events.push("first:end");
      });
      await entered;
      const second = withWorkspaceLock(
        siblingRoot,
        async () => {
          events.push("second");
        },
        { timeoutMs: 1000, retryDelayMs: 5 },
      );
      await new Promise((resolve) => setTimeout(resolve, 30));
      assert.deepStrictEqual(events, ["first:start"]);
      releaseFirst?.();
      await Promise.all([first, second]);
    });

    assert.deepStrictEqual(events, ["first:start", "first:end", "second"]);
  });

  test("reports a live Python owner without deleting its lock", async function () {
    this.timeout(20_000);
    const repoRoot = await createRepoRoot();
    const script = `
import sys
from pathlib import Path
from freecm.workspace_lock import workspace_mutation_lock
with workspace_mutation_lock(Path(sys.argv[1])):
    print("ready", flush=True)
    sys.stdin.readline()
`;
    const child = spawnPython(script, [repoRoot]);
    try {
      await waitForOutput(child, "ready");
      const pythonOwner = JSON.parse(
        await fs.readFile(ownerPath(repoRoot), "utf8"),
      ) as Record<string, unknown>;
      assert.strictEqual(pythonOwner.implementation, "python");
      const pythonOwnerPid = pythonOwner.pid;
      assert.ok(
        typeof pythonOwnerPid === "number" &&
          Number.isInteger(pythonOwnerPid) &&
          pythonOwnerPid > 0,
      );
      await assert.rejects(
        () =>
          withWorkspaceLock(repoRoot, async () => undefined, {
            timeoutMs: 80,
            retryDelayMs: 10,
          }),
        new RegExp(`current owner: pid=${pythonOwnerPid}.*implementation=python`),
      );
      assert.strictEqual(await exists(lockPath(repoRoot)), true);
      child.stdin.write("\n");
      assert.strictEqual(await waitForExit(child), 0);
      assert.strictEqual(await exists(lockPath(repoRoot)), false);
    } finally {
      if (child.exitCode === null) {
        child.kill();
        await waitForExit(child).catch(() => undefined);
      }
    }
  });

  test("Python reports a live VS Code owner", async function () {
    this.timeout(20_000);
    const repoRoot = await createRepoRoot();
    const script = `
import sys
from pathlib import Path
from freecm.workspace_lock import workspace_mutation_lock
try:
    with workspace_mutation_lock(Path(sys.argv[1]), timeout_seconds=0.08):
        raise AssertionError("unexpected acquisition")
except TimeoutError as error:
    print(str(error))
`;

    await withWorkspaceLock(repoRoot, async () => {
      const result = await runPython(script, [repoRoot]);
      assert.strictEqual(result.exitCode, 0, result.stderr);
      assert.match(
        result.stdout,
        new RegExp(`current owner: pid=${process.pid}.*implementation=vscode`),
      );
      assert.strictEqual(await exists(lockPath(repoRoot)), true);
    });
  });

  test("does not probe process identity for a current-process owner", async () => {
    const repoRoot = await createRepoRoot();
    await withWorkspaceLock(repoRoot, async () => undefined);
    const canonicalLockPath = lockPath(await fs.realpath(repoRoot));
    await fs.mkdir(canonicalLockPath);
    await fs.writeFile(
      path.join(canonicalLockPath, WORKSPACE_LOCK_PROTOCOL.ownerFileName),
      `${JSON.stringify(ownerData("probe-owner", process.pid))}\n`,
    );
    let probeCount = 0;
    let restore = (): void => undefined;

    if (process.platform === "linux") {
      const mutableFs = require("fs/promises") as {
        readFile: (...args: unknown[]) => Promise<unknown>;
      };
      const originalReadFile = mutableFs.readFile;
      mutableFs.readFile = async (...args: unknown[]) => {
        if (String(args[0]) === `/proc/${process.pid}/stat`) {
          probeCount += 1;
        }
        return originalReadFile(...args);
      };
      restore = () => {
        mutableFs.readFile = originalReadFile;
      };
    } else if (process.platform === "darwin" || process.platform === "win32") {
      const mutableChildProcess = require("child_process") as {
        spawn: (...args: unknown[]) => unknown;
      };
      const originalSpawn = mutableChildProcess.spawn;
      mutableChildProcess.spawn = (...args: unknown[]) => {
        const command = String(args[0]);
        if (command === "ps" || command === "powershell.exe") {
          probeCount += 1;
        }
        return originalSpawn(...args);
      };
      restore = () => {
        mutableChildProcess.spawn = originalSpawn;
      };
    }

    try {
      await assert.rejects(
        () =>
          withWorkspaceLock(repoRoot, async () => undefined, {
            timeoutMs: 320,
            retryDelayMs: 5,
          }),
        /Unable to acquire workspace lock/,
      );
      assert.strictEqual(probeCount, 0);
    } finally {
      restore();
      await fs.rm(canonicalLockPath, { recursive: true, force: true });
    }
  });

  test("recovers a Python lock after process crash", async () => {
    const repoRoot = await createRepoRoot();
    const script = `
import os
import sys
from pathlib import Path
from freecm.workspace_lock import workspace_mutation_lock
with workspace_mutation_lock(Path(sys.argv[1])):
    print("ready", flush=True)
    os._exit(0)
`;
    const child = spawnPython(script, [repoRoot]);
    await waitForOutput(child, "ready");
    assert.strictEqual(await waitForExit(child), 0);
    assert.strictEqual(await exists(lockPath(repoRoot)), true);

    await withWorkspaceLock(repoRoot, async () => undefined, {
      timeoutMs: 500,
      retryDelayMs: 5,
    });
    assert.strictEqual(await exists(lockPath(repoRoot)), false);
  });

  test("Python recovers a VS Code lock after process crash", async () => {
    const repoRoot = await createRepoRoot();
    const modulePath = path.join(
      FREECM_ROOT,
      "vscode-extension",
      "out",
      "workspaceLock.js",
    );
    const script = `
const { withWorkspaceLock } = require(process.argv[1]);
withWorkspaceLock(process.argv[2], async () => {
  console.log("ready");
  process.exit(0);
});
`;
    const child = spawn(process.execPath, ["-e", script, modulePath, repoRoot], {
      cwd: FREECM_ROOT,
      shell: false,
      stdio: "pipe",
      windowsHide: true,
    });
    await waitForOutput(child, "ready");
    assert.strictEqual(await waitForExit(child), 0);
    assert.strictEqual(await exists(lockPath(repoRoot)), true);

    const result = await runPython(
      `
import sys
from pathlib import Path
from freecm.workspace_lock import workspace_mutation_lock
with workspace_mutation_lock(Path(sys.argv[1]), timeout_seconds=0.5):
    print("acquired")
`,
      [repoRoot],
    );
    assert.strictEqual(result.exitCode, 0, result.stderr);
    assert.match(result.stdout, /acquired/);
    assert.strictEqual(await exists(lockPath(repoRoot)), false);
  });

  test("recovers invalid metadata only after initialization grace", async () => {
    const repoRoot = await createRepoRoot();
    await fs.mkdir(lockPath(repoRoot));

    await assert.rejects(
      () =>
        withWorkspaceLock(repoRoot, async () => undefined, {
          timeoutMs: 20,
          retryDelayMs: 5,
          initializationGraceMs: 1000,
        }),
      /missing or invalid owner metadata/,
    );
    assert.strictEqual(await exists(lockPath(repoRoot)), true);

    const oldTime = new Date(Date.now() - 10000);
    await fs.utimes(lockPath(repoRoot), oldTime, oldTime);
    await withWorkspaceLock(repoRoot, async () => undefined, {
      timeoutMs: 500,
      retryDelayMs: 5,
      initializationGraceMs: 10,
    });
    assert.strictEqual(await exists(lockPath(repoRoot)), false);
  });

  test("recovers an orphan reclaimer claim", async () => {
    const repoRoot = await createRepoRoot();
    const canonicalLockPath = lockPath(repoRoot);
    await fs.mkdir(canonicalLockPath);
    const probe = spawn(process.execPath, ["-e", ""], { stdio: "ignore" });
    const probePid = probe.pid;
    assert.notStrictEqual(probePid, undefined);
    await new Promise<void>((resolve, reject) => {
      probe.on("error", reject);
      probe.on("close", () => resolve());
    });
    await fs.writeFile(
      path.join(canonicalLockPath, ".reclaim"),
      `${JSON.stringify(ownerData("orphan-reclaimer", probePid!))}\n`,
    );
    const oldTime = new Date(Date.now() - 10000);
    await fs.utimes(canonicalLockPath, oldTime, oldTime);

    await withWorkspaceLock(repoRoot, async () => undefined, {
      timeoutMs: 500,
      retryDelayMs: 5,
      initializationGraceMs: 10,
    });
    assert.strictEqual(await exists(canonicalLockPath), false);
  });

  test("does not remove an active reclaimer at confirmation timeout", async () => {
    const repoRoot = await createRepoRoot();
    const realRepoRoot = await fs.realpath(repoRoot);
    const canonicalOwnerPath = ownerPath(realRepoRoot);
    const claimPath = path.join(lockPath(realRepoRoot), ".reclaim");
    const mutableFs = require("fs/promises") as {
      open: (...args: unknown[]) => Promise<{
        close: () => Promise<void>;
      }>;
    };
    const originalOpen = mutableFs.open;
    let injected = false;
    mutableFs.open = async (...args: unknown[]) => {
      const handle = await originalOpen(...args);
      if (!injected && String(args[0]) === canonicalOwnerPath) {
        injected = true;
        const originalClose = handle.close.bind(handle);
        handle.close = async () => {
          await originalClose();
          await fs.writeFile(
            claimPath,
            `${JSON.stringify(ownerData("active-reclaimer", process.pid))}\n`,
            { flag: "wx" },
          );
        };
      }
      return handle;
    };

    try {
      await assert.rejects(
        () =>
          withWorkspaceLock(repoRoot, async () => undefined, {
            timeoutMs: 20,
            retryDelayMs: 5,
          }),
        /active reclaimer/,
      );
      assert.strictEqual(await exists(claimPath), true);
      assert.strictEqual(await exists(canonicalOwnerPath), true);
      assert.strictEqual(
        (await fs.readdir(lockPath(realRepoRoot))).filter((name) =>
          name.startsWith(".abandoned."),
        ).length,
        1,
      );
      await fs.rm(claimPath);
      await withWorkspaceLock(realRepoRoot, async () => undefined, {
        timeoutMs: 500,
        retryDelayMs: 5,
      });
      assert.strictEqual(await exists(lockPath(realRepoRoot)), false);
    } finally {
      mutableFs.open = originalOpen;
      await fs.rm(lockPath(realRepoRoot), { recursive: true, force: true });
    }
  });

  test("abandon marker cannot delete or abandon a replacement generation", async () => {
    const repoRoot = await createRepoRoot();
    const realRepoRoot = await fs.realpath(repoRoot);
    const canonicalLockPath = lockPath(realRepoRoot);
    const canonicalOwnerPath = ownerPath(realRepoRoot);
    const claimPath = path.join(canonicalLockPath, ".reclaim");
    const retiredPath = `${canonicalLockPath}.retired`;
    const mutableFs = require("fs/promises") as {
      open: (...args: unknown[]) => Promise<{
        close: () => Promise<void>;
      }>;
    };
    const originalOpen = mutableFs.open;
    let injectedClaim = false;
    let replacedGeneration = false;
    mutableFs.open = async (...args: unknown[]) => {
      const requestedPath = String(args[0]);
      if (
        !replacedGeneration &&
        requestedPath.startsWith(`${canonicalLockPath}${path.sep}.abandoned.`)
      ) {
        replacedGeneration = true;
        await fs.rename(canonicalLockPath, retiredPath);
        await fs.mkdir(canonicalLockPath);
        await fs.writeFile(
          canonicalOwnerPath,
          `${JSON.stringify(ownerData("replacement-owner", process.pid))}\n`,
        );
      }
      const handle = await originalOpen(...args);
      if (!injectedClaim && requestedPath === canonicalOwnerPath) {
        injectedClaim = true;
        const originalClose = handle.close.bind(handle);
        handle.close = async () => {
          await originalClose();
          await fs.writeFile(
            claimPath,
            `${JSON.stringify(ownerData("active-reclaimer", process.pid))}\n`,
            { flag: "wx" },
          );
        };
      }
      return handle;
    };

    try {
      await assert.rejects(
        () =>
          withWorkspaceLock(repoRoot, async () => undefined, {
            timeoutMs: 20,
            retryDelayMs: 5,
          }),
        /active reclaimer/,
      );
      const replacementOwner = JSON.parse(
        await fs.readFile(canonicalOwnerPath, "utf8"),
      ) as { token: string };
      assert.strictEqual(replacementOwner.token, "replacement-owner");
      await assert.rejects(
        () =>
          withWorkspaceLock(realRepoRoot, async () => undefined, {
            timeoutMs: 20,
            retryDelayMs: 5,
          }),
        /current owner/,
      );
      const ownerAfterTimeout = JSON.parse(
        await fs.readFile(canonicalOwnerPath, "utf8"),
      ) as { token: string };
      assert.strictEqual(ownerAfterTimeout.token, "replacement-owner");
    } finally {
      mutableFs.open = originalOpen;
      await fs.rm(canonicalLockPath, { recursive: true, force: true });
      await fs.rm(retiredPath, { recursive: true, force: true });
    }
  });

  test("publishes reclaim claims atomically after candidate completion", async function () {
    this.timeout(10000);
    const repoRoot = await createRepoRoot();
    const realRepoRoot = await fs.realpath(repoRoot);
    const canonicalLockPath = lockPath(realRepoRoot);
    const claimPath = path.join(canonicalLockPath, ".reclaim");
    await fs.mkdir(canonicalLockPath);
    const oldTime = new Date(Date.now() - 10000);
    await fs.utimes(canonicalLockPath, oldTime, oldTime);

    const mutableFs = require("fs/promises") as {
      link: (existingPath: string, newPath: string) => Promise<void>;
    };
    const originalLink = mutableFs.link;
    let claimLinkCount = 0;
    let releaseFirstLink: (() => void) | undefined;
    let firstLinkStarted: (() => void) | undefined;
    let secondLinkCompleted: (() => void) | undefined;
    const releaseFirst = new Promise<void>((resolve) => {
      releaseFirstLink = resolve;
    });
    const firstStarted = new Promise<void>((resolve) => {
      firstLinkStarted = resolve;
    });
    const secondCompleted = new Promise<void>((resolve) => {
      secondLinkCompleted = resolve;
    });
    mutableFs.link = async (existingPath: string, newPath: string) => {
      if (newPath !== claimPath) {
        return originalLink(existingPath, newPath);
      }
      claimLinkCount += 1;
      if (claimLinkCount === 1) {
        assert.strictEqual(await exists(claimPath), false);
        const candidateText = await fs.readFile(existingPath, "utf8");
        assert.doesNotThrow(() => JSON.parse(candidateText));
        firstLinkStarted?.();
        await releaseFirst;
      }
      await originalLink(existingPath, newPath);
      if (claimLinkCount === 2) {
        secondLinkCompleted?.();
      }
    };

    let activeOperations = 0;
    let maximumActiveOperations = 0;
    const operation = async (): Promise<void> => {
      activeOperations += 1;
      maximumActiveOperations = Math.max(maximumActiveOperations, activeOperations);
      await new Promise((resolve) => setTimeout(resolve, 10));
      activeOperations -= 1;
    };

    try {
      const first = withWorkspaceLock(realRepoRoot, operation, {
        timeoutMs: 1000,
        retryDelayMs: 5,
        initializationGraceMs: 10,
      });
      await firstStarted;
      assert.strictEqual(await exists(claimPath), false);
      await new Promise((resolve) => setTimeout(resolve, 20));
      const second = withWorkspaceLock(realRepoRoot, operation, {
        timeoutMs: 1000,
        retryDelayMs: 5,
        initializationGraceMs: 10,
      });
      await secondCompleted;
      releaseFirstLink?.();
      await Promise.all([first, second]);
      assert.strictEqual(maximumActiveOperations, 1);
      assert.ok(claimLinkCount >= 2);
      assert.strictEqual(await exists(canonicalLockPath), false);
    } finally {
      releaseFirstLink?.();
      mutableFs.link = originalLink;
      await fs.rm(canonicalLockPath, { recursive: true, force: true });
    }
  });

  test("owner write failure does not delete a replacement lock", async () => {
    const repoRoot = await createRepoRoot();
    const realRepoRoot = await fs.realpath(repoRoot);
    const canonicalLockPath = lockPath(realRepoRoot);
    const canonicalOwnerPath = ownerPath(realRepoRoot);
    const retiredPath = `${canonicalLockPath}.retired`;
    const mutableFs = require("fs/promises") as {
      open: (...args: unknown[]) => Promise<unknown>;
    };
    const originalOpen = mutableFs.open;
    mutableFs.open = async (...args: unknown[]) => {
      if (String(args[0]) === canonicalOwnerPath) {
        await fs.rename(canonicalLockPath, retiredPath);
        await fs.mkdir(canonicalLockPath);
        await fs.writeFile(
          canonicalOwnerPath,
          `${JSON.stringify(ownerData("replacement-owner", process.pid))}\n`,
        );
        throw new Error("owner write failed");
      }
      return originalOpen(...args);
    };

    try {
      await assert.rejects(
        () => withWorkspaceLock(repoRoot, async () => undefined),
        /owner write failed/,
      );
      assert.strictEqual(await exists(canonicalOwnerPath), true);
    } finally {
      mutableFs.open = originalOpen;
      await fs.rm(canonicalLockPath, { recursive: true, force: true });
      await fs.rm(retiredPath, { recursive: true, force: true });
    }
  });

  test("does not release a lock whose owner token changed", async () => {
    const repoRoot = await createRepoRoot();

    await assert.rejects(
      () =>
        withWorkspaceLock(repoRoot, async () => {
          const owner = JSON.parse(
            await fs.readFile(ownerPath(repoRoot), "utf8"),
          ) as Record<string, unknown>;
          owner.token = "replacement-owner";
          await fs.writeFile(ownerPath(repoRoot), `${JSON.stringify(owner)}\n`);
        }),
      /ownership changed before release/,
    );
    assert.strictEqual(await exists(lockPath(repoRoot)), true);
    await fs.rm(lockPath(repoRoot), { recursive: true, force: true });
  });
});
