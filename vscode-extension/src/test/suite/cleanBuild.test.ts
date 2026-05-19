import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import { cleanBuild } from "../../cleanBuild";

async function createRepoRoot(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "repoconfigsmgr-clean-"));
}

async function exists(filePath: string): Promise<boolean> {
  try {
    await fs.lstat(filePath);
    return true;
  } catch {
    return false;
  }
}

async function createSymlink(
  context: Mocha.Context,
  target: string,
  linkPath: string,
  type?: "dir" | "file" | "junction",
): Promise<void> {
  try {
    await fs.symlink(target, linkPath, type);
  } catch (error) {
    if (isWindowsSymlinkPermissionError(error)) {
      context.skip();
    }
    throw error;
  }
}

function isWindowsSymlinkPermissionError(error: unknown): boolean {
  return process.platform === "win32" &&
    error instanceof Error &&
    "code" in error &&
    (error as NodeJS.ErrnoException).code === "EPERM";
}

suite("clean build", () => {
  test("removes only non-preserved direct children under build", async () => {
    const repoRoot = await createRepoRoot();
    const buildDir = path.join(repoRoot, "build");
    await fs.mkdir(path.join(buildDir, "dependency_seed_repos"), { recursive: true });
    await fs.mkdir(path.join(buildDir, "dependency_source_roots"), { recursive: true });
    await fs.mkdir(path.join(buildDir, "generated", "nested"), { recursive: true });
    await fs.writeFile(path.join(buildDir, "generated", "nested", "file.txt"), "x");
    await fs.writeFile(path.join(buildDir, "artifact.txt"), "x");
    await fs.writeFile(path.join(repoRoot, "DerivedData"), "outside");

    const result = await cleanBuild(repoRoot);

    assert.deepStrictEqual(result.removed, ["build/artifact.txt", "build/generated"]);
    assert.deepStrictEqual(result.preserved, [
      "build/dependency_seed_repos",
      "build/dependency_source_roots",
    ]);
    assert.strictEqual(await exists(path.join(buildDir, "dependency_seed_repos")), true);
    assert.strictEqual(await exists(path.join(buildDir, "dependency_source_roots")), true);
    assert.strictEqual(await exists(path.join(buildDir, "generated")), false);
    assert.strictEqual(await exists(path.join(buildDir, "artifact.txt")), false);
    assert.strictEqual(await exists(path.join(repoRoot, "DerivedData")), true);
  });

  test("does nothing when build directory is missing", async () => {
    const repoRoot = await createRepoRoot();

    const result = await cleanBuild(repoRoot);

    assert.deepStrictEqual(result, { removed: [], preserved: [] });
  });

  test("rejects symlinked build directory", async function () {
    const repoRoot = await createRepoRoot();
    const external = await createRepoRoot();
    await createSymlink(
      this,
      external,
      path.join(repoRoot, "build"),
      process.platform === "win32" ? "junction" : "dir",
    );

    await assert.rejects(
      () => cleanBuild(repoRoot),
      /Refusing to clean symlinked build directory/,
    );
  });

  test("rejects non-directory build path", async () => {
    const repoRoot = await createRepoRoot();
    await fs.writeFile(path.join(repoRoot, "build"), "not a directory");

    await assert.rejects(
      () => cleanBuild(repoRoot),
      /Refusing to clean non-directory build path/,
    );
  });

  test("unlinks symlink children without following them", async function () {
    const repoRoot = await createRepoRoot();
    const buildDir = path.join(repoRoot, "build");
    const external = await createRepoRoot();
    await fs.mkdir(buildDir);
    await fs.writeFile(path.join(external, "keep.txt"), "keep");
    await createSymlink(
      this,
      external,
      path.join(buildDir, "linked-artifact"),
      process.platform === "win32" ? "junction" : "dir",
    );

    const result = await cleanBuild(repoRoot);

    assert.deepStrictEqual(result.removed, ["build/linked-artifact"]);
    assert.strictEqual(await exists(path.join(buildDir, "linked-artifact")), false);
    assert.strictEqual(await exists(path.join(external, "keep.txt")), true);
  });
});
