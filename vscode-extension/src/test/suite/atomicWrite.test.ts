import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import { __test, atomicWriteText } from "../../atomicWrite";

async function createTempRoot(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "freecm-atomic-write-"));
}

async function exists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

function atomicSidecarDirectory(filePath: string): string {
  return path.join(path.dirname(filePath), ".freecm", "atomic");
}

async function temporaryFiles(directory: string, baseName: string): Promise<string[]> {
  return (await fs.readdir(directory)).filter(
    (entry) => entry.startsWith(`.${baseName}.`) && entry.endsWith(".tmp"),
  );
}

async function assertNoNewTemporaryFiles(filePath: string): Promise<void> {
  const sidecarDirectory = atomicSidecarDirectory(filePath);
  const baseName = path.basename(filePath);
  assert.deepStrictEqual(await temporaryFiles(sidecarDirectory, baseName), []);
}

suite("atomic write", () => {
  test("replaces text without leaving temporary or lock paths", async () => {
    const root = await createTempRoot();
    const target = path.join(root, "source_roots.lock.jsonc");

    await atomicWriteText(target, "first\n");
    await atomicWriteText(target, "second\n");

    assert.strictEqual(await fs.readFile(target, "utf8"), "second\n");
    await assertNoNewTemporaryFiles(target);
  });

  test("ignores a crash-left legacy vscode lock", async () => {
    const root = await createTempRoot();
    const target = path.join(root, "source_roots.lock.jsonc");
    const lockPath = path.join(
      atomicSidecarDirectory(target),
      `.${path.basename(target)}.vscode.lock`,
    );
    await fs.mkdir(lockPath, { recursive: true });

    await atomicWriteText(target, "replacement\n");

    assert.strictEqual(await fs.readFile(target, "utf8"), "replacement\n");
    assert.strictEqual(await exists(lockPath), true);
    await assertNoNewTemporaryFiles(target);
  });

  test("ignores another generation temporary file", async () => {
    const root = await createTempRoot();
    const target = path.join(root, "source_roots.lock.jsonc");
    const sidecarDirectory = atomicSidecarDirectory(target);
    const legacyTempPath = path.join(
      sidecarDirectory,
      `.${path.basename(target)}.old-generation.tmp`,
    );
    await fs.mkdir(sidecarDirectory, { recursive: true });
    await fs.writeFile(legacyTempPath, "partial\n", "utf8");

    await atomicWriteText(target, "complete\n");

    assert.strictEqual(await fs.readFile(target, "utf8"), "complete\n");
    assert.strictEqual(await fs.readFile(legacyTempPath, "utf8"), "partial\n");
    assert.deepStrictEqual(await temporaryFiles(sidecarDirectory, path.basename(target)), [
      path.basename(legacyTempPath),
    ]);
  });

  test("keeps existing content when staging cannot start", async () => {
    const root = await createTempRoot();
    const target = path.join(root, "source_roots.lock.jsonc");
    const freecmPath = path.join(root, ".freecm");
    await fs.writeFile(target, "original\n", "utf8");
    await fs.writeFile(freecmPath, "not-a-directory\n", "utf8");

    await assert.rejects(() => atomicWriteText(target, "replacement\n"));

    assert.strictEqual(await fs.readFile(target, "utf8"), "original\n");
  });

  test("keeps concurrent writer outputs complete", async () => {
    const root = await createTempRoot();
    const target = path.join(root, "source_roots.lock.jsonc");
    const values = Array.from({ length: 12 }, (_, index) =>
      JSON.stringify({ index, payload: "x".repeat(1024) }, null, 2) + "\n",
    );

    await Promise.all(values.map((value) => atomicWriteText(target, value)));

    const finalText = await fs.readFile(target, "utf8");
    assert.ok(values.includes(finalText));
    assert.doesNotThrow(() => JSON.parse(finalText));
    await assertNoNewTemporaryFiles(target);
  });

  test("retries transient Windows rename failures with bounded delays", async () => {
    const delays: number[] = [];
    let attempts = 0;
    await __test.renameReplacingWithRetry("source", "target", {
      platform: "win32",
      rename: async () => {
        attempts += 1;
        if (attempts < 3) {
          throw Object.assign(new Error("busy"), { code: "EPERM" });
        }
      },
      delay: async (milliseconds) => {
        delays.push(milliseconds);
      },
    });

    assert.strictEqual(attempts, 3);
    assert.deepStrictEqual(delays, [10, 20]);
  });

  test("does not retry non-transient rename failures", async () => {
    let attempts = 0;
    const failure = Object.assign(new Error("missing"), { code: "ENOENT" });
    await assert.rejects(
      () =>
        __test.renameReplacingWithRetry("source", "target", {
          platform: "win32",
          rename: async () => {
            attempts += 1;
            throw failure;
          },
          delay: async () => undefined,
        }),
      (error) => error === failure,
    );

    assert.strictEqual(attempts, 1);
  });

  test("does not retry transient Windows errors on other platforms", async () => {
    const delays: number[] = [];
    let attempts = 0;
    const failure = Object.assign(new Error("permission denied"), { code: "EPERM" });
    await assert.rejects(
      () =>
        __test.renameReplacingWithRetry("source", "target", {
          platform: "linux",
          rename: async () => {
            attempts += 1;
            throw failure;
          },
          delay: async (milliseconds) => {
            delays.push(milliseconds);
          },
        }),
      (error) => error === failure,
    );

    assert.strictEqual(attempts, 1);
    assert.deepStrictEqual(delays, []);
  });

  test("stops retrying transient rename failures after the bounded backoff", async () => {
    const delays: number[] = [];
    let attempts = 0;
    const failure = Object.assign(new Error("busy"), { code: "EBUSY" });
    await assert.rejects(
      () =>
        __test.renameReplacingWithRetry("source", "target", {
          platform: "win32",
          rename: async () => {
            attempts += 1;
            throw failure;
          },
          delay: async (milliseconds) => {
            delays.push(milliseconds);
          },
        }),
      (error) => error === failure,
    );

    assert.strictEqual(attempts, 6);
    assert.deepStrictEqual(delays, [10, 20, 40, 80, 160]);
  });
});
