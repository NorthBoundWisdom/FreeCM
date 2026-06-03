import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import { atomicWriteText } from "../../atomicWrite";

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

async function tempFiles(directory: string, baseName: string): Promise<string[]> {
  return (await fs.readdir(directory)).filter(
    (entry) => entry.startsWith(`.${baseName}.`) && entry.endsWith(".tmp"),
  );
}

suite("atomic write", () => {
  test("replaces text without leaving temporary or lock paths", async () => {
    const root = await createTempRoot();
    const target = path.join(root, "source_roots.lock.jsonc");

    await atomicWriteText(target, "first\n");
    await atomicWriteText(target, "second\n");

    assert.strictEqual(await fs.readFile(target, "utf8"), "second\n");
    assert.deepStrictEqual(await tempFiles(root, path.basename(target)), []);
    assert.strictEqual(
      await exists(path.join(root, ".source_roots.lock.jsonc.vscode.lock")),
      false,
    );
  });

  test("keeps existing content when the lock cannot be acquired", async () => {
    const root = await createTempRoot();
    const target = path.join(root, "source_roots.lock.jsonc");
    const lockPath = path.join(root, ".source_roots.lock.jsonc.vscode.lock");
    await fs.writeFile(target, "original\n", "utf8");
    await fs.mkdir(lockPath);

    await assert.rejects(
      () =>
        atomicWriteText(target, "replacement\n", {
          lockTimeoutMs: 50,
          retryDelayMs: 5,
        }),
      /Unable to acquire lock/,
    );

    assert.strictEqual(await fs.readFile(target, "utf8"), "original\n");
    await fs.rm(lockPath, { recursive: true, force: true });
  });

  test("serializes concurrent writers into complete file contents", async () => {
    const root = await createTempRoot();
    const target = path.join(root, "source_roots.lock.jsonc");
    const values = Array.from({ length: 12 }, (_, index) =>
      JSON.stringify({ index, payload: "x".repeat(1024) }, null, 2) + "\n",
    );

    await Promise.all(values.map((value) => atomicWriteText(target, value)));

    const finalText = await fs.readFile(target, "utf8");
    assert.ok(values.includes(finalText));
    assert.doesNotThrow(() => JSON.parse(finalText));
    assert.deepStrictEqual(await tempFiles(root, path.basename(target)), []);
  });
});
