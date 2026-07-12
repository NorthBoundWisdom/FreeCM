import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import {
  FileSystemProbe,
  RepoWorkspaceFolder,
  foldersWithCapability,
  inspectWorkspaceCapabilities,
  resolveTargetFolder,
  workspaceCapabilities,
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
  const root = await fs.mkdtemp(path.join(os.tmpdir(), `freecm-${name}-`));
  return { name, fsPath: root };
}

async function touch(filePath: string): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, "");
}

suite("workspace discovery", () => {
  test("detects workspace capabilities independently", async () => {
    const folder = await createFolder("capable");
    await fs.mkdir(path.join(folder.fsPath, "FreeCM"), { recursive: true });
    await fs.mkdir(
      path.join(folder.fsPath, "build", "dependency_seed_repos"),
      { recursive: true },
    );
    await touch(path.join(folder.fsPath, "configs", "source_root_workflow.py"));
    await touch(path.join(folder.fsPath, "configs", "freecm.commands.jsonc"));
    await touch(path.join(folder.fsPath, "source_roots.lock.jsonc.in"));

    assert.deepStrictEqual(
      await inspectWorkspaceCapabilities(folder, nodeFileSystem),
      {
        folder,
        hasSeedRepositories: true,
        hasWorkflowScript: true,
        hasLockFile: true,
        hasRepoCommandManifest: true,
      },
    );
  });

  test("keeps workflow detection scoped to configs entrypoint", async () => {
    const folder = await createFolder("scripts-only");
    await touch(path.join(folder.fsPath, "scripts", "source_root_workflow.py"));
    await touch(path.join(folder.fsPath, "source_roots.lock.jsonc.in"));

    const capabilities = await inspectWorkspaceCapabilities(
      folder,
      nodeFileSystem,
    );
    assert.strictEqual(capabilities.hasWorkflowScript, false);
    assert.strictEqual(capabilities.hasLockFile, true);
  });

  test("filters folders by selected capability", async () => {
    const withWorkflow = await createFolder("workflow");
    const withCommands = await createFolder("commands");
    const withSeeds = await createFolder("seeds");
    await touch(
      path.join(withWorkflow.fsPath, "configs", "source_root_workflow.py"),
    );
    await touch(
      path.join(withCommands.fsPath, "configs", "freecm.commands.jsonc"),
    );
    await fs.mkdir(
      path.join(withSeeds.fsPath, "build", "dependency_seed_repos"),
      { recursive: true },
    );

    const capabilities = await workspaceCapabilities(
      [withCommands, withWorkflow, withSeeds],
      nodeFileSystem,
    );
    assert.deepStrictEqual(
      foldersWithCapability(
        capabilities,
        (capability) => capability.hasWorkflowScript,
      ),
      [withWorkflow],
    );
    assert.deepStrictEqual(
      foldersWithCapability(
        capabilities,
        (capability) => capability.hasRepoCommandManifest,
      ),
      [withCommands],
    );
    assert.deepStrictEqual(
      foldersWithCapability(
        capabilities,
        (capability) => capability.hasSeedRepositories,
      ),
      [withSeeds],
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
