import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import {
  prependPathEnvironment,
  terminalPathEntries,
  terminalPathEnvironmentForRepo,
} from "../../terminalPath";

async function createRepoRoot(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "freecm-terminal-path-"));
}

async function writeJsonc(filePath: string, value: unknown): Promise<void> {
  await fs.writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

suite("terminal path", () => {
  test("resolves common and platform entries relative to repo root", () => {
    assert.deepStrictEqual(
      terminalPathEntries(
        {
          terminalPath: {
            common: ["tools/bin", "/opt/shared/bin"],
            mac: ["mac/bin"],
            linux: ["linux/bin"],
          },
        },
        "/repo",
        "mac",
        "/repo/source_roots.lock.jsonc",
      ),
      [
        path.resolve("/repo", "tools/bin"),
        "/opt/shared/bin",
        path.resolve("/repo", "mac/bin"),
      ],
    );
  });

  test("prepends entries to POSIX and Windows PATH", () => {
    assert.deepStrictEqual(
      prependPathEnvironment(["/repo/tools/bin"], "darwin", {
        PATH: "/usr/bin",
      }),
      { PATH: "/repo/tools/bin:/usr/bin" },
    );
    assert.deepStrictEqual(
      prependPathEnvironment(["C:\\repo\\tools\\bin"], "win32", {
        Path: "C:\\Windows",
      }),
      { Path: "C:\\repo\\tools\\bin;C:\\Windows" },
    );
  });

  test("active lock takes precedence over template lock", async () => {
    const repoRoot = await createRepoRoot();
    await writeJsonc(path.join(repoRoot, "source_roots.lock.jsonc.in"), {
      terminalPath: {
        common: ["template/bin"],
      },
    });
    await writeJsonc(path.join(repoRoot, "source_roots.lock.jsonc"), {
      terminalPath: {
        common: ["active/bin"],
      },
    });

    const result = await terminalPathEnvironmentForRepo(repoRoot, "linux", {
      PATH: "/usr/bin",
    });

    assert.deepStrictEqual(result.entries, [path.join(repoRoot, "active/bin")]);
    assert.deepStrictEqual(result.env, {
      PATH: `${path.join(repoRoot, "active/bin")}:/usr/bin`,
    });
  });

  test("template lock is used when active lock is missing", async () => {
    const repoRoot = await createRepoRoot();
    await writeJsonc(path.join(repoRoot, "source_roots.lock.jsonc.in"), {
      terminalPath: {
        common: ["template/bin"],
      },
    });

    const result = await terminalPathEnvironmentForRepo(repoRoot, "linux", {
      PATH: "/usr/bin",
    });

    assert.deepStrictEqual(result.entries, [
      path.join(repoRoot, "template/bin"),
    ]);
  });

  test("invalid terminalPath is rejected", () => {
    assert.throws(
      () =>
        terminalPathEntries(
          {
            terminalPath: {
              ios: ["tools/ios/bin"],
            },
          },
          "/repo",
          "mac",
          "/repo/source_roots.lock.jsonc",
        ),
      /unexpected keys: ios/,
    );
    assert.throws(
      () =>
        terminalPathEntries(
          {
            terminalPath: {
              common: "tools/bin",
            },
          },
          "/repo",
          "mac",
          "/repo/source_roots.lock.jsonc",
        ),
      /terminalPath\.common.*string array/,
    );
  });
});
