import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import {
  DEFAULT_CODE_COUNT_EXCLUDE_PATHS,
  normalizeCodeCountTarget,
  normalizeCodeCountExcludePaths,
  parseCodeCountExcludePathsText,
} from "../../codeCounter/settings";
import { LineCounter } from "../../codeCounter/lineCounter";
import { buildCodeCountReport } from "../../codeCounter/report";
import { clearCodeCountFileCache, countCode } from "../../codeCounter/engine";
import {
  clearLanguageTableCache,
  createLineCounterTable,
} from "../../codeCounter/languageDiscovery";
import { captureExtensionPerformance } from "../../performanceMetrics";

suite("code counter", () => {
  test("counts C++ code comments blanks and raw strings", () => {
    const code = `
      void main () {
        int x = 0;
        int y = 0; // code line

        // comment
        const char* str = "text";

        /*
          comment
          comment
        */
        int z = 100; /* code line
          comment
        */

        /**
          comment
          comment
         */
        const char* hstr = R"(
          // not comment
          text

          /*
           not comment
          */

        )";
      }
    `;
    const counter = new LineCounter(
      "cpp",
      ["//"],
      [["/*", "*/"]],
      [['R"(', ')"']],
      [
        ["'", "'"],
        ['"', '"'],
        ["/*", "*/"],
        ["/**", " */"],
      ],
    );

    assert.deepStrictEqual(countFields(counter.count(code, false)), {
      blank: 4,
      code: 15,
      comment: 11,
    });
  });

  test("does not treat block comment markers inside strings as comments", () => {
    const code = `
      Console.WriteLine("line 1");
      Console.WriteLine("line 2 /*");
      Console.WriteLine("line 3");
      Console.WriteLine("line 4");
    `;
    const counter = new LineCounter(
      "c#",
      ["//"],
      [["/*", "*/"]],
      [],
      [['"', '"']],
    );

    assert.deepStrictEqual(countFields(counter.count(code, false)), {
      blank: 1,
      code: 4,
      comment: 0,
    });
  });

  test("counts Python triple quoted docstrings as comments", () => {
    const code = `
"""
Module docsstring

Blaahblaah
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MyContainer:
    """A custom container"""

    field_a: int
    """Field a blaah blaah"""

    field_b: str
    """Field b has a very long
    docstring"""


def __main__():
    print("""
        This is a very long text
        that is multiline
        it should be counted as code.
        """)
`;
    const counter = new LineCounter(
      "python",
      ["#"],
      [['"""', '"""']],
      [['"""', '"""']],
      [['"', '"']],
      true,
    );

    assert.deepStrictEqual(countFields(counter.count(code, false)), {
      blank: 8,
      code: 11,
      comment: 9,
    });
  });

  test("counts Kotlin files with line comments block comments and raw strings", () => {
    const code = `
fun main() {
    val text = """
        // not a comment
        /* not a comment */
    """
    val url = "https://example.com" // code line
    /*
      block comment
    */
    println(text)
}
`;
    const counter = new LineCounter(
      "Kotlin",
      ["//"],
      [["/*", "*/"]],
      [['"""', '"""']],
      [
        ['"', '"'],
        ["'", "'"],
      ],
    );

    assert.deepStrictEqual(countFields(counter.count(code, false)), {
      blank: 1,
      code: 8,
      comment: 3,
    });
  });

  test("builds language recursive directory tree and file report tables", () => {
    const targetUri = vscode.Uri.file("/repo/App");
    const report = buildCodeCountReport({
      generatedAt: new Date("2026-05-23T00:00:00Z"),
      targetUri,
      reportUri: vscode.Uri.file("/repo/App/.freecm/counts/report/results.md"),
      files: [
        {
          uri: vscode.Uri.file("/repo/App/Sources/Core/main.cpp"),
          filename: "/repo/App/Sources/Core/main.cpp",
          language: "C++",
          code: 10,
          comment: 2,
          blank: 1,
        },
        {
          uri: vscode.Uri.file("/repo/App/Sources/UI/view.cpp"),
          filename: "/repo/App/Sources/UI/view.cpp",
          language: "C++",
          code: 5,
          comment: 1,
          blank: 1,
        },
        {
          uri: vscode.Uri.file("/repo/App/scripts/tool.py"),
          filename: "/repo/App/scripts/tool.py",
          language: "Python",
          code: 3,
          comment: 4,
          blank: 2,
        },
      ],
    });

    assert.deepStrictEqual(report.total, {
      name: "Total",
      files: 3,
      code: 18,
      comment: 7,
      blank: 4,
      total: 29,
    });
    assert.deepStrictEqual(report.directories, [
      {
        name: ".",
        files: 3,
        code: 18,
        comment: 7,
        blank: 4,
        total: 29,
      },
      {
        name: "Sources",
        files: 2,
        code: 15,
        comment: 3,
        blank: 2,
        total: 20,
      },
      {
        name: "Sources/Core",
        files: 1,
        code: 10,
        comment: 2,
        blank: 1,
        total: 13,
      },
      {
        name: "Sources/UI",
        files: 1,
        code: 5,
        comment: 1,
        blank: 1,
        total: 7,
      },
      {
        name: "scripts",
        files: 1,
        code: 3,
        comment: 4,
        blank: 2,
        total: 9,
      },
    ]);
    assert.ok(report.markdown.includes("| C++ | 2 | 15 | 3 | 2 | 20 |"));
    assert.ok(report.markdown.includes("| . | 3 | 18 | 7 | 4 | 29 |"));
    assert.ok(report.markdown.includes("| Sources | 2 | 15 | 3 | 2 | 20 |"));
    assert.ok(
      report.markdown.includes(
        "| &nbsp;&nbsp;Core | 1 | 10 | 2 | 1 | 13 |",
      ),
    );
    assert.ok(
      report.markdown.includes("| &nbsp;&nbsp;UI | 1 | 5 | 1 | 1 | 7 |"),
    );
    assert.ok(
      report.markdown.includes("| scripts/tool.py | Python | 3 | 4 | 2 | 9 |"),
    );
  });

  test("normalizes stored target paths inside workspace only", () => {
    assert.strictEqual(
      normalizeCodeCountTarget("/repo/App", "/repo/App/Sources"),
      "/repo/App/Sources",
    );
    assert.strictEqual(
      normalizeCodeCountTarget("/repo/App", "/repo/Other"),
      "/repo/App",
    );
    assert.strictEqual(
      normalizeCodeCountTarget("/repo/App", undefined),
      "/repo/App",
    );
  });

  test("normalizes code count exclude paths", () => {
    assert.deepStrictEqual(DEFAULT_CODE_COUNT_EXCLUDE_PATHS, [
      "build",
      "FreeCM",
      "thirdparty",
      "Downloads",
    ]);
    assert.deepStrictEqual(
      normalizeCodeCountExcludePaths([
        " build ",
        "Sources\\Generated\\",
        "Generated",
        "generated",
        "Downloads/",
      ]),
      ["build", "Sources/Generated", "Generated", "Downloads"],
    );
    assert.deepStrictEqual(
      parseCodeCountExcludePathsText("build\nSources\\Generated\n\nGenerated\n")
        .paths,
      ["build", "Sources/Generated", "Generated"],
    );
    assert.match(
      parseCodeCountExcludePathsText("build\n*.tmp").error ?? "",
      /Line 2: Wildcards and negation are not supported/,
    );
    assert.match(
      parseCodeCountExcludePathsText("# generated files").error ?? "",
      /Line 1: Comments are not supported/,
    );
  });

  test("counts files using built-in custom and gitignore excludes", async function () {
    this.timeout(10_000);
    const workspaceRoot = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-code-count-"),
    );
    await fs.mkdir(path.join(workspaceRoot, "Sources"), { recursive: true });
    await fs.mkdir(path.join(workspaceRoot, "Sources", "generated"), {
      recursive: true,
    });
    await fs.mkdir(path.join(workspaceRoot, "Sources", "localIgnored"), {
      recursive: true,
    });
    await fs.mkdir(path.join(workspaceRoot, "Sources", "rootScoped"), {
      recursive: true,
    });
    await fs.mkdir(path.join(workspaceRoot, "Sources", "shaders"), {
      recursive: true,
    });
    await fs.mkdir(path.join(workspaceRoot, "Nested", "Generated"), {
      recursive: true,
    });
    await fs.mkdir(path.join(workspaceRoot, "Other", "localIgnored"), {
      recursive: true,
    });
    await fs.mkdir(path.join(workspaceRoot, "build"), { recursive: true });
    await fs.mkdir(
      path.join(workspaceRoot, "FreeCM", "vscode-extension", "src"),
      {
        recursive: true,
      },
    );
    await fs.mkdir(path.join(workspaceRoot, "ignored"), { recursive: true });
    await fs.mkdir(path.join(workspaceRoot, "thirdparty", "Lib"), {
      recursive: true,
    });
    await fs.mkdir(path.join(workspaceRoot, "Downloads"), { recursive: true });
    await fs.writeFile(
      path.join(workspaceRoot, ".gitignore"),
      "ignored/\ngenerated/\n.freecm/counts/\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "main.cpp"),
      "int main() {\n  return 0;\n}\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "App.kt"),
      'fun main() {\n  println("hi")\n}\n',
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "shaders", "pcbatlas_line.vert"),
      "#version 450\nlayout(location = 0) in vec2 position;\n\n// Vertex shader main\nvoid main() {\n  gl_Position = vec4(position, 0.0, 1.0);\n}\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "shaders", "pcbatlas_line.frag"),
      "#version 450\nlayout(location = 0) out vec4 outColor;\n/* Fragment shader */\nvoid main() {\n  outColor = vec4(1.0);\n}\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "metadata.json"),
      '{\n  "count": false\n}\n',
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "tsconfig.json"),
      '{\n  "compilerOptions": {}\n}\n',
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "config.yaml"),
      "name: ignored\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "notes.md"),
      "# Ignored\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "layout.xml"),
      "<root />\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "icon.svg"),
      "<svg></svg>\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "index.html"),
      "<html></html>\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "style.css"),
      "body {\n  color: red;\n}\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "theme.scss"),
      "$color: red;\nbody {\n  color: $color;\n}\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "theme.sass"),
      "$color: red\nbody\n  color: $color\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "theme.less"),
      "@color: red;\nbody {\n  color: @color;\n}\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "settings.ini"),
      "[ignored]\nvalue=true\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "pyproject.toml"),
      "[tool.ignored]\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "readme.rst"),
      "Ignored\n=======\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", ".dockerignore"),
      "build\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", ".gitignore"),
      "localIgnored/\n/rootScoped/\n*.tmp\n!restored/\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "requirements.txt"),
      "pytest\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "application.properties"),
      "enabled=false\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "setup.bat"),
      "echo off\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "generated", "auto.cpp"),
      "int generated = 1;\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "localIgnored", "skip.cpp"),
      "int local_ignored = 1;\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Sources", "rootScoped", "skip.cpp"),
      "int root_scoped = 1;\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Nested", "Generated", "more.cpp"),
      "int more_generated = 1;\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Other", "localIgnored", "keep.cpp"),
      "int scoped_elsewhere = 1;\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "ignored", "skip.cpp"),
      "int ignored = 1;\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "build", "generated.cpp"),
      "int generated = 1;\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(
        workspaceRoot,
        "FreeCM",
        "vscode-extension",
        "src",
        "extension.ts",
      ),
      "export const countedByMistake = true;\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "thirdparty", "Lib", "vendor.cpp"),
      "int vendor = 1;\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, "Downloads", "download.cpp"),
      "int download = 1;\n",
      "utf8",
    );
    await fs.mkdir(path.join(workspaceRoot, ".freecm", "counts", "old"), {
      recursive: true,
    });
    await fs.mkdir(path.join(workspaceRoot, ".git", "hooks"), {
      recursive: true,
    });
    await fs.writeFile(
      path.join(workspaceRoot, ".freecm", "counts", "old", "results.md"),
      "```cpp\nint generated = 1;\n```\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceRoot, ".git", "hooks", "pre-commit.cpp"),
      "int hook = 1;\n",
      "utf8",
    );
    const originalFindFiles = vscode.workspace.findFiles;

    try {
      (
        vscode.workspace as unknown as {
          findFiles: typeof vscode.workspace.findFiles;
        }
      ).findFiles = async () =>
        [
          ".gitignore",
          path.join("Sources", "main.cpp"),
          path.join("Sources", "App.kt"),
          path.join("Sources", "shaders", "pcbatlas_line.vert"),
          path.join("Sources", "shaders", "pcbatlas_line.frag"),
          path.join("Sources", "metadata.json"),
          "tsconfig.json",
          path.join("Sources", "config.yaml"),
          path.join("Sources", "notes.md"),
          path.join("Sources", "layout.xml"),
          path.join("Sources", "icon.svg"),
          path.join("Sources", "index.html"),
          path.join("Sources", "style.css"),
          path.join("Sources", "theme.scss"),
          path.join("Sources", "theme.sass"),
          path.join("Sources", "theme.less"),
          path.join("Sources", "settings.ini"),
          path.join("Sources", "pyproject.toml"),
          path.join("Sources", "readme.rst"),
          path.join("Sources", ".dockerignore"),
          path.join("Sources", ".gitignore"),
          path.join("Sources", "requirements.txt"),
          path.join("Sources", "application.properties"),
          path.join("Sources", "setup.bat"),
          path.join("Sources", "generated", "auto.cpp"),
          path.join("Sources", "localIgnored", "skip.cpp"),
          path.join("Sources", "rootScoped", "skip.cpp"),
          path.join("Nested", "Generated", "more.cpp"),
          path.join("Other", "localIgnored", "keep.cpp"),
          path.join("ignored", "skip.cpp"),
          path.join("build", "generated.cpp"),
          path.join("FreeCM", "vscode-extension", "src", "extension.ts"),
          path.join("thirdparty", "Lib", "vendor.cpp"),
          path.join("Downloads", "download.cpp"),
          path.join(".freecm", "counts", "old", "results.md"),
          path.join(".git", "hooks", "pre-commit.cpp"),
        ].map((relativePath) =>
          vscode.Uri.file(path.join(workspaceRoot, relativePath)),
        );

      const report = await countCode({
        workspaceRoot,
        targetPath: workspaceRoot,
        outputRoot: path.join(workspaceRoot, ".freecm", "counts"),
        extensions: [],
        excludePaths: [
          ...DEFAULT_CODE_COUNT_EXCLUDE_PATHS,
          "generated",
          "Generated",
        ],
      });

      assert.deepStrictEqual(
        report.files
          .map((file) => path.relative(workspaceRoot, file.filename))
          .sort(),
        [
          path.join("Other", "localIgnored", "keep.cpp"),
          path.join("Sources", "App.kt"),
          path.join("Sources", "main.cpp"),
          path.join("Sources", "shaders", "pcbatlas_line.frag"),
          path.join("Sources", "shaders", "pcbatlas_line.vert"),
        ],
      );
      assert.deepStrictEqual(report.excludedPaths, [
        "build",
        "FreeCM",
        "thirdparty",
        "Downloads",
        "generated",
      ]);
      const markdown = await fs.readFile(report.reportUri.fsPath, "utf8");
      assert.ok(
        markdown.includes("Sources/main.cpp") ||
          markdown.includes("Sources\\main.cpp"),
      );
      assert.ok(
        markdown.includes("Sources/App.kt") ||
          markdown.includes("Sources\\App.kt"),
      );
      assert.ok(
        markdown.includes("Sources/shaders/pcbatlas_line.frag") ||
          markdown.includes("Sources\\shaders\\pcbatlas_line.frag"),
      );
      assert.ok(
        markdown.includes("Sources/shaders/pcbatlas_line.vert") ||
          markdown.includes("Sources\\shaders\\pcbatlas_line.vert"),
      );
      assert.ok(
        markdown.includes("Other/localIgnored/keep.cpp") ||
          markdown.includes("Other\\localIgnored\\keep.cpp"),
      );
      assert.ok(!markdown.includes("ignored/skip.cpp"));
      assert.ok(!markdown.includes("ignored\\skip.cpp"));
      assert.ok(!markdown.includes("Sources/generated/auto.cpp"));
      assert.ok(!markdown.includes("Sources/localIgnored/skip.cpp"));
      assert.ok(!markdown.includes("Sources/rootScoped/skip.cpp"));
      assert.ok(!markdown.includes("Nested/Generated/more.cpp"));
      assert.ok(!markdown.includes("Downloads/download.cpp"));
      assert.ok(markdown.includes("| Kotlin | 1 | 3 | 0 | 1 | 4 |"));
      assert.ok(markdown.includes("| Shader | 2 | 10 | 2 | 3 | 15 |"));
      assert.ok(!markdown.includes("| reStructuredText |"));
      assert.ok(!markdown.includes("| Ignore |"));
      assert.ok(!markdown.includes("| JSON |"));
      assert.ok(!markdown.includes("| pip requirements |"));
      assert.ok(!markdown.includes("| Properties |"));
      assert.ok(!markdown.includes("| Batch |"));
      assert.ok(!markdown.includes("| CSS |"));
      const excludedSectionIndex = markdown.indexOf(
        "## Excluded Formats And Paths",
      );
      assert.ok(excludedSectionIndex > markdown.indexOf("## Files"));
      assert.ok(markdown.includes("- HTML (.html, .htm)"));
      assert.ok(markdown.includes("- Batch (.bat, .cmd)"));
      assert.ok(markdown.includes("- CSS/styles (.css, .scss, .sass, .less)"));
      assert.ok(
        markdown.includes(
          "- Ignore files (.gitignore, .ignore, .dockerignore, .eslintignore, .npmignore)",
        ),
      );
      assert.ok(
        markdown.includes(
          "- INI/config/properties (.ini, .cfg, .conf, .config, .properties, .toml)",
        ),
      );
      assert.ok(markdown.includes("- build"));
      assert.ok(markdown.includes("- FreeCM"));
      assert.ok(markdown.includes("- thirdparty"));
      assert.ok(markdown.includes("- Downloads"));
      assert.ok(markdown.includes("- generated"));
      assert.ok(
        markdown.includes("- pip requirements (requirements*.txt, Pipfile)"),
      );
      assert.ok(markdown.includes("- reStructuredText (.rst)"));
      assert.ok(markdown.includes("- YAML (.yaml, .yml)"));
    } finally {
      (
        vscode.workspace as unknown as {
          findFiles: typeof vscode.workspace.findFiles;
        }
      ).findFiles = originalFindFiles;
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });

  test("keeps the counting engine dynamic and language tables input-cached", async () => {
    const extensionSource = await fs.readFile(
      path.resolve(__dirname, "../../../src/extension.ts"),
      "utf8",
    );
    assert.ok(extensionSource.includes('await import("./codeCounter/engine")'));
    assert.ok(!extensionSource.includes('from "./codeCounter/engine"'));
    clearLanguageTableCache();
    const first = await createLineCounterTable([], {});
    const cached = await createLineCounterTable([], {});
    const changed = await createLineCounterTable([], { SpecialFile: "cpp" });
    assert.strictEqual(cached, first);
    assert.notStrictEqual(changed, first);
    assert.ok(first.candidateGlob().includes("*.[cC][pP][pP]"));
    assert.ok(first.candidateGlob().includes("[cC][mM][aA][kK][eE]"));
    assert.ok(first.candidateGlob().includes("[jJ][aA][kK][eE]"));
    assert.strictEqual(first.getCounter("/repo/MAIN.CPP")?.name, "C++");
    assert.strictEqual(first.getCounter("/repo/CMakeLists.txt")?.name, "CMake");
    assert.notStrictEqual(first.candidateGlob(), "**/*");
  });

  test("applies gitignore globs and negation with target-scoped discovery", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-count-ignore-"));
    const ignored = path.join(workspaceRoot, "Sources", "drop.gen.cpp");
    const restored = path.join(workspaceRoot, "Sources", "keep.gen.cpp");
    await fs.mkdir(path.dirname(ignored), { recursive: true });
    await fs.writeFile(path.join(workspaceRoot, ".gitignore"), "**/*.gen.cpp\n!Sources/keep.gen.cpp\n", "utf8");
    await fs.writeFile(ignored, "int drop = 1;\n", "utf8");
    await fs.writeFile(restored, "int keep = 1;\n", "utf8");
    const originalFindFiles = vscode.workspace.findFiles;
    try {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = async () =>
        [path.join(workspaceRoot, ".gitignore"), ignored, restored].map((file) => vscode.Uri.file(file));
      const report = await countCode({
        workspaceRoot,
        targetPath: workspaceRoot,
        outputRoot: path.join(workspaceRoot, ".freecm", "counts"),
        extensions: [],
      });
      assert.deepStrictEqual(report.files.map((file) => file.filename), [restored]);
    } finally {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = originalFindFiles;
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });

  test("does not load negation rules from a parent-ignored directory", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-count-ignore-parent-"));
    const ignoredDirectory = path.join(workspaceRoot, "ignored");
    const ignoredFile = path.join(ignoredDirectory, "keep.cpp");
    const visibleFile = path.join(workspaceRoot, "visible.cpp");
    await fs.mkdir(ignoredDirectory, { recursive: true });
    await fs.writeFile(path.join(workspaceRoot, ".gitignore"), "ignored/\n", "utf8");
    await fs.writeFile(path.join(ignoredDirectory, ".gitignore"), "!keep.cpp\n", "utf8");
    await fs.writeFile(ignoredFile, "int ignored = 1;\n", "utf8");
    await fs.writeFile(visibleFile, "int visible = 1;\n", "utf8");
    const originalFindFiles = vscode.workspace.findFiles;
    try {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = async () =>
        [path.join(workspaceRoot, ".gitignore"), path.join(ignoredDirectory, ".gitignore"), ignoredFile, visibleFile]
          .map((file) => vscode.Uri.file(file));
      const report = await countCode({
        workspaceRoot,
        targetPath: workspaceRoot,
        outputRoot: path.join(workspaceRoot, ".freecm", "counts"),
        extensions: [],
        maxFiles: 1,
      });
      assert.deepStrictEqual(report.files.map((file) => file.filename), [visibleFile]);
    } finally {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = originalFindFiles;
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });

  test("discovers mixed-case supported filenames on case-sensitive platforms", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-count-case-"));
    const mixedCpp = path.join(workspaceRoot, "main.CpP");
    const mixedCmake = path.join(workspaceRoot, "cMAkeLists.TxT");
    await fs.writeFile(mixedCpp, "int value = 1;\n", "utf8");
    await fs.writeFile(mixedCmake, "project(SampleApp)\n", "utf8");
    const originalFindFiles = vscode.workspace.findFiles;
    let candidatePattern = "";
    try {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = async (include) => {
        const pattern = include instanceof vscode.RelativePattern ? include.pattern : String(include);
        if (pattern === "**/.gitignore") return [];
        candidatePattern = pattern;
        return [mixedCpp, mixedCmake].map((file) => vscode.Uri.file(file));
      };
      const report = await countCode({
        workspaceRoot,
        targetPath: workspaceRoot,
        outputRoot: path.join(workspaceRoot, ".freecm", "counts"),
        extensions: [],
      });
      assert.ok(candidatePattern.includes("*.[cC][pP][pP]"));
      assert.ok(candidatePattern.includes("[cC][mM][aA][kK][eE]"));
      assert.deepStrictEqual(report.files.map((file) => file.filename).sort(), [mixedCmake, mixedCpp].sort());
    } finally {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = originalFindFiles;
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });

  test("caches unchanged trees and reports bounded code-count performance", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-count-perf-"));
    const outputRoot = path.join(workspaceRoot, ".freecm", "counts");
    const files = Array.from({ length: 100 }, (_, index) =>
      path.join(workspaceRoot, "Sources", `file-${index}.cpp`),
    );
    await fs.mkdir(path.join(workspaceRoot, "Sources"), { recursive: true });
    await Promise.all(files.map((file) => fs.writeFile(file, "int value = 1;\n", "utf8")));
    const originalFindFiles = vscode.workspace.findFiles;
    const patterns: string[] = [];
    try {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = async (include) => {
        patterns.push(include instanceof vscode.RelativePattern ? include.pattern : String(include));
        return files.map((file) => vscode.Uri.file(file));
      };
      clearCodeCountFileCache();
      clearLanguageTableCache();
      const run = () => countCode({ workspaceRoot, targetPath: workspaceRoot, outputRoot, extensions: [], maxConcurrentReads: 8 });
      const cold = await captureExtensionPerformance("code-count-100-cold", run);
      const cached = await captureExtensionPerformance("code-count-100-cached", run);
      await fs.writeFile(files[0], "int changed_value = 2;\n", "utf8");
      const changed = await captureExtensionPerformance("code-count-100-one-change", run);
      assert.strictEqual(cold.result.files.length, 100);
      assert.ok(cold.report.filesystemReads >= 203);
      assert.ok(cold.report.peakConcurrentReads <= 8);
      assert.ok(
        cached.report.filesystemReads <= 105,
        JSON.stringify({ cold: cold.report, cached: cached.report }),
      );
      assert.ok(
        changed.report.filesystemReads <= 106,
        JSON.stringify({ cached: cached.report, changed: changed.report }),
      );
      assert.ok(changed.report.filesystemReads > cached.report.filesystemReads);
      for (const report of [cold.report, cached.report, changed.report]) assert.ok(report.durationMs < 10_000);
      assert.ok(patterns.some((pattern) => pattern.includes("*.[cC][pP][pP]")));
      assert.ok(!patterns.includes("**/*"));
    } finally {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = originalFindFiles;
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });

  test("keys cached counts by line-affecting options", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-count-options-"));
    const incomplete = path.join(workspaceRoot, "incomplete.cpp");
    const complete = path.join(workspaceRoot, "complete.cpp");
    await fs.writeFile(incomplete, "int incomplete = 1;", "utf8");
    await fs.writeFile(complete, "int complete = 1;\n", "utf8");
    const originalFindFiles = vscode.workspace.findFiles;
    try {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = async () =>
        [incomplete, complete].map((file) => vscode.Uri.file(file));
      clearCodeCountFileCache();
      const options = {
        workspaceRoot,
        targetPath: workspaceRoot,
        outputRoot: path.join(workspaceRoot, ".freecm", "counts"),
        extensions: [] as const,
      };
      const included = await countCode({ ...options, includeIncompleteLine: true });
      const excluded = await countCode({ ...options, includeIncompleteLine: false });
      assert.strictEqual(included.files.find((file) => file.filename === incomplete)?.code, 1);
      assert.strictEqual(excluded.files.find((file) => file.filename === incomplete)?.code, 0);
    } finally {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = originalFindFiles;
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });

  test("cancels in-flight discovery before writing a report", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-count-cancel-"));
    const sourceFile = path.join(workspaceRoot, "source.cpp");
    const outputRoot = path.join(workspaceRoot, ".freecm", "counts");
    await fs.writeFile(sourceFile, "int value = 1;\n", "utf8");
    const originalFindFiles = vscode.workspace.findFiles;
    let calls = 0;
    let markStarted: (() => void) | undefined;
    let release: (() => void) | undefined;
    const started = new Promise<void>((resolve) => { markStarted = resolve; });
    const gate = new Promise<void>((resolve) => { release = resolve; });
    try {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = async () => {
        calls += 1;
        if (calls === 1) return [];
        markStarted?.();
        await gate;
        return [vscode.Uri.file(sourceFile)];
      };
      const cancellation = new vscode.CancellationTokenSource();
      const pending = countCode({
        workspaceRoot,
        targetPath: workspaceRoot,
        outputRoot,
        extensions: [],
        cancellationToken: cancellation.token,
      });
      await started;
      cancellation.cancel();
      release?.();
      await assert.rejects(pending, (error) => error instanceof vscode.CancellationError);
      await assert.rejects(fs.access(outputRoot));
      cancellation.dispose();
    } finally {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = originalFindFiles;
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });

  test("surfaces limits skipped files cancellation and report retention", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-count-limits-"));
    const outputRoot = path.join(workspaceRoot, ".freecm", "counts");
    const small = path.join(workspaceRoot, "small.cpp");
    const large = path.join(workspaceRoot, "large.cpp");
    await fs.writeFile(small, "int small = 1;\n", "utf8");
    await fs.writeFile(large, "x".repeat(128), "utf8");
    for (const name of ["20200101_000000", "20210101_000000", "20220101_000000"]) {
      const directory = path.join(outputRoot, name);
      await fs.mkdir(directory, { recursive: true });
      await fs.writeFile(path.join(directory, "results.md"), "old report\n", "utf8");
      await fs.writeFile(path.join(directory, ".freecm-code-count-report"), "1\n", "utf8");
    }
    await fs.rm(path.join(outputRoot, "20210101_000000", ".freecm-code-count-report"));
    await fs.writeFile(
      path.join(outputRoot, "20210101_000000", "results.md"),
      "# FreeCM Code Count\n\nlegacy report\n",
      "utf8",
    );
    const unmanagedTimestampDirectory = path.join(outputRoot, "20230101_000000");
    await fs.mkdir(unmanagedTimestampDirectory, { recursive: true });
    await fs.writeFile(path.join(unmanagedTimestampDirectory, "user.txt"), "preserve\n", "utf8");
    const originalFindFiles = vscode.workspace.findFiles;
    try {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = async () =>
        [small, large].map((file) => vscode.Uri.file(file));
      clearCodeCountFileCache();
      const report = await countCode({ workspaceRoot, targetPath: workspaceRoot, outputRoot, extensions: [], maxFileBytes: 64, reportRetention: 2 });
      assert.deepStrictEqual(report.skippedFiles, [{ filename: large, reason: "large" }]);
      assert.ok(report.markdown.includes("Skipped 1"));
      const retained = (await fs.readdir(outputRoot)).filter((name) => /^\d{8}_\d{6}$/.test(name));
      assert.strictEqual(retained.length, 3);
      assert.strictEqual(await fs.readFile(path.join(unmanagedTimestampDirectory, "user.txt"), "utf8"), "preserve\n");
      await assert.rejects(
        countCode({ workspaceRoot, targetPath: workspaceRoot, outputRoot, extensions: [], maxFiles: 1 }),
        /more than maxFiles=1/,
      );
      const cancellation = new vscode.CancellationTokenSource();
      cancellation.cancel();
      await assert.rejects(
        countCode({ workspaceRoot, targetPath: workspaceRoot, outputRoot, extensions: [], cancellationToken: cancellation.token }),
        (error) => error instanceof vscode.CancellationError,
      );
      cancellation.dispose();
    } finally {
      (vscode.workspace as unknown as { findFiles: typeof vscode.workspace.findFiles }).findFiles = originalFindFiles;
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });
});

function countFields(count: { code: number; comment: number; blank: number }) {
  return {
    blank: count.blank,
    code: count.code,
    comment: count.comment,
  };
}
