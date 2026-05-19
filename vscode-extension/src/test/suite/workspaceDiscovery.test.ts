import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import {
  FileSystemProbe,
  RepoWorkspaceFolder,
  eligibleRepoFolders,
  isEligibleRepoFolder,
  resolveTargetFolder,
} from "../../workspaceDiscovery";

const nodeFileSystem: FileSystemProbe = {
  async exists(filePath: string): Promise<boolean> {
    try {
      await fs.access(filePath);
      return true;
    } catch {
      return false;
    }
  },
  async isDirectory(filePath: string): Promise<boolean> {
    try {
      return (await fs.stat(filePath)).isDirectory();
    } catch {
      return false;
    }
  },
};

async function createFolder(name: string): Promise<RepoWorkspaceFolder> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), `repoconfigsmgr-${name}-`));
  return { name, fsPath: root };
}

async function touch(filePath: string): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, "");
}

async function makeEligible(folder: RepoWorkspaceFolder): Promise<void> {
  await fs.mkdir(path.join(folder.fsPath, "RepoConfigsMgr"), { recursive: true });
  await touch(path.join(folder.fsPath, "configs", "source_root_workflow.py"));
  await touch(path.join(folder.fsPath, "source_roots.lock.jsonc.in"));
}

suite("workspace discovery", () => {
  test("requires RepoConfigsMgr, a lock file, and configs/source_root_workflow.py", async () => {
    const folder = await createFolder("eligible");
    await makeEligible(folder);

    assert.strictEqual(await isEligibleRepoFolder(folder, nodeFileSystem), true);
  });

  test("does not accept scripts/source_root_workflow.py without configs entrypoint", async () => {
    const folder = await createFolder("scripts-only");
    await fs.mkdir(path.join(folder.fsPath, "RepoConfigsMgr"), { recursive: true });
    await touch(path.join(folder.fsPath, "scripts", "source_root_workflow.py"));
    await touch(path.join(folder.fsPath, "source_roots.lock.jsonc.in"));

    assert.strictEqual(await isEligibleRepoFolder(folder, nodeFileSystem), false);
  });

  test("filters eligible folders", async () => {
    const eligible = await createFolder("eligible");
    const ineligible = await createFolder("ineligible");
    await makeEligible(eligible);

    assert.deepStrictEqual(
      await eligibleRepoFolders([ineligible, eligible], nodeFileSystem),
      [eligible],
    );
  });

  test("prefers active eligible folder", async () => {
    const first = await createFolder("first");
    const second = await createFolder("second");

    assert.deepStrictEqual(resolveTargetFolder([first, second], second), {
      kind: "folder",
      folder: second,
    });
  });

  test("selects the single eligible folder automatically", async () => {
    const folder = await createFolder("single");

    assert.deepStrictEqual(resolveTargetFolder([folder], undefined), {
      kind: "folder",
      folder,
    });
  });

  test("requires user choice for multiple eligible folders without active match", async () => {
    const first = await createFolder("first");
    const second = await createFolder("second");

    assert.deepStrictEqual(resolveTargetFolder([first, second], undefined), {
      kind: "choose",
      folders: [first, second],
    });
  });
});
