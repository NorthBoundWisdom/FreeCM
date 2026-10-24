import * as assert from "assert";
import { execFile } from "child_process";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import { promisify } from "util";
import { manualAll, manualDependency, usePinned } from "../../lockWorkflow";
import { parseLockText } from "../../lockSchemaValidation";

const run = promisify(execFile);
async function git(cwd: string, ...args: string[]): Promise<string> {
  const result = await run("git", args, { cwd, env: { ...process.env,
    GIT_AUTHOR_NAME: "Test", GIT_AUTHOR_EMAIL: "test@example.invalid",
    GIT_COMMITTER_NAME: "Test", GIT_COMMITTER_EMAIL: "test@example.invalid" } });
  return result.stdout.trim();
}

suite("public seeds", () => {
  let root: string;
  let host: string;
  let seed: string;
  let activePath: string;
  let data: any;
  setup(async () => {
    root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "freecm-public-")));
    host = path.join(root, "Host");
    seed = path.join(root, "public", "LibA");
    await fs.mkdir(host, { recursive: true });
    await fs.mkdir(seed, { recursive: true });
    await git(seed, "init");
    await fs.writeFile(path.join(seed, "source.txt"), "original\n");
    await git(seed, "add", ".");
    await git(seed, "commit", "-m", "fixture");
    const remote = "https://example.invalid/LibA.git";
    await git(seed, "remote", "add", "origin", remote);
    data = { schemaVersion: 5, depsMode: "pinned", depsManualPath: { LibA: "" },
      dependencies: { LibA: { remote, commit: await git(seed, "rev-parse", "HEAD") } } };
    await fs.writeFile(path.join(host, "source_roots.lock.jsonc.in"), JSON.stringify(data));
    data.dependencies.LibA.publicSeedPath = "../public/LibA";
    activePath = path.join(host, "source_roots.lock.jsonc");
    await fs.writeFile(activePath, JSON.stringify(data));
  });
  teardown(async () => { await fs.rm(root, { recursive: true, force: true }); });

  test("m creates an independent local checkout offline and preserves origin", async () => {
    await manualDependency(host, "LibA");
    const local = path.join(host, "build", "dependency_seed_repos", "LibA");
    const active = JSON.parse(await fs.readFile(activePath, "utf8"));
    assert.strictEqual(active.depsManualPath.LibA, "build/dependency_seed_repos/LibA");
    assert.strictEqual(active.dependencies.LibA.publicSeedPath, "../public/LibA");
    assert.strictEqual(await git(local, "rev-parse", "HEAD"), data.dependencies.LibA.commit);
    assert.strictEqual(await git(local, "config", "remote.origin.url"), data.dependencies.LibA.remote);
    assert.notStrictEqual(await git(local, "rev-parse", "--absolute-git-dir"), await git(seed, "rev-parse", "--absolute-git-dir"));
    await fs.writeFile(path.join(local, "source.txt"), "editing\n");
    assert.strictEqual(await fs.readFile(path.join(seed, "source.txt"), "utf8"), "original\n");
    // Existing local checkouts must work even if the public seed is unavailable.
    await fs.rename(seed, `${seed}-away`);
    await manualDependency(host, "LibA");
    assert.strictEqual(await fs.readFile(path.join(local, "source.txt"), "utf8"), "editing\n");
    await manualAll(host);
    assert.strictEqual(await fs.readFile(path.join(local, "source.txt"), "utf8"), "editing\n");
  });

  test("manual all creates missing copies and use pinned preserves machine paths", async () => {
    await manualAll(host);
    assert.strictEqual(await git(path.join(host, "build/dependency_seed_repos/LibA"), "rev-parse", "HEAD"), data.dependencies.LibA.commit);
    await usePinned(host);
    const active = JSON.parse(await fs.readFile(activePath, "utf8"));
    assert.strictEqual(active.depsMode, "pinned");
    assert.strictEqual(active.dependencies.LibA.publicSeedPath, "../public/LibA");
    assert.strictEqual(active.depsManualPath.LibA, "");
  });

  test("copy failure leaves the active lock unchanged", async () => {
    const before = await fs.readFile(activePath, "utf8");
    await fs.writeFile(path.join(seed, "source.txt"), "dirty\n");
    await assert.rejects(manualDependency(host, "LibA"), /publicSeedPath is dirty/);
    assert.strictEqual(await fs.readFile(activePath, "utf8"), before);
    await assert.rejects(fs.stat(path.join(host, "build/dependency_seed_repos/LibA")), { code: "ENOENT" });
  });

  test("manual detection is limited to this lock's declared paths", () => {
    data.depsMode = "manual";
    data.depsManualPath.LibA = seed;
    assert.throws(() => parseLockText(JSON.stringify(data), activePath), /declared publicSeedPath/);
    data.depsManualPath.LibA = `${seed}-editable`;
    assert.doesNotThrow(() => parseLockText(JSON.stringify(data), activePath));
    data.depsManualPath.LibA = seed;
    delete data.dependencies.LibA.publicSeedPath;
    assert.doesNotThrow(() => parseLockText(JSON.stringify(data), activePath));
  });

  test("a local symlink into the public seed is rejected before copying", async function () {
    const local = path.join(host, "build/dependency_seed_repos/LibA");
    await fs.mkdir(path.dirname(local), { recursive: true });
    try { await fs.symlink(seed, local, process.platform === "win32" ? "junction" : "dir"); }
    catch { this.skip(); }
    await assert.rejects(manualDependency(host, "LibA"), /declared publicSeedPath/);
    assert.strictEqual(JSON.parse(await fs.readFile(activePath, "utf8")).depsMode, "pinned");
  });
});
