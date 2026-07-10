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
});
