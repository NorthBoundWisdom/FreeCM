import * as assert from "assert";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { createCodeCountClassifier } from "../../codeCounter/classification";
import { clearCodeCountFileCache, countCode } from "../../codeCounter/engine";
import { buildCodeCountReport } from "../../codeCounter/report";

suite("code count production and test classification", () => {
  test("recognizes cross-language test layouts and names with boundaries", () => {
    const classify = createCodeCountClassifier();
    const tests = [
      "test/main.cpp", "Tests/Unit/support.hpp", "src/tests/helper.rs",
      "src/feature_tests/helper.rs", "src/__tests__/setup.ts", "src/__mocks__/api.ts",
      "spec/helpers/setup.rb", "specs/main.cpp", "e2e/browser.ts",
      "test_package/main.cpp", "testing/helpers.py", "src/SampleApp.Tests/helper.cs",
      "SampleAppTests/Helpers.swift", "SampleAppUITests/Support.swift",
      "UITests/Support.swift", "APITests/Helper.swift", "src/HTTPTests.cs",
      "app/src/test/java/sample/Helper.java", "app/src/androidTest/java/sample/Helper.java",
      "app/src/testFixtures/kotlin/sample/Helper.kt", "configs/test_workflow.py",
      "src/conftest.py", "src/TEST_geometry.cpp", "src/geometry_test.cpp",
      "src/geometry_tests.rs", "src/geometry_test.go", "src/math.test.ts",
      "src/math.spec.tsx", "src/GeometryTests.cs", "src/GeometryTest.java",
      "src/GeometryTests.swift", "src/geometry-test.gd", "TESTS\\unit\\helper.cpp",
    ];
    for (const filename of tests) assert.strictEqual(classify(filename), "test", filename);
    const production = [
      "src/main.cpp", "Sources/SampleApp/Model.swift", "app/src/main/java/sample/App.java",
      "src/contest.cpp", "src/latest.ts", "src/TestFlight.swift", "src/Testimonials.tsx",
      "src/testing_service.py", "src/testimony.cpp", "Sources/Contest/Model.swift",
      "Sources/Latest/Model.swift", "src/backtest.py", "scripts/build.py", "CMakeLists.txt",
      "CONTEST/Model.swift",
    ];
    for (const filename of production) assert.strictEqual(classify(filename), "production", filename);
  });

  test("explicit workspace-relative paths override defaults without prefix collisions", () => {
    const classify = createCodeCountClassifier({
      testPaths: ["Quality/Checks/", "scripts/check.py"],
      productionPaths: ["Tests/Runtime", "src/test_protocol.cpp"],
    });
    assert.strictEqual(classify("Quality/Checks/helpers.cpp"), "test");
    assert.strictEqual(classify("quality\\checks\\helper.cpp"), "test");
    assert.strictEqual(classify("Quality/ChecksExtra/main.cpp"), "production");
    assert.strictEqual(classify("scripts/check.py"), "test");
    assert.strictEqual(classify("scripts/check.py.extra"), "production");
    assert.strictEqual(classify("Tests/Runtime/test_api.cpp"), "production");
    assert.strictEqual(classify("Tests/RuntimeExtra/helper.cpp"), "test");
    assert.strictEqual(classify("src/test_protocol.cpp"), "production");
    assert.strictEqual(createCodeCountClassifier({
      testPaths: ["Quality"], productionPaths: ["Quality/Runtime"],
    })("Quality/Runtime/main.cpp"), "production");
    for (const invalid of ["../tests", "/tests", "C:\\tests", "**/tests", "", "a//b"]) {
      assert.throws(() => createCodeCountClassifier({ testPaths: [invalid] }), /Invalid.*testPaths/);
      assert.throws(() => createCodeCountClassifier({ productionPaths: [invalid] }), /Invalid.*productionPaths/);
    }
  });

  test("reports mixed categories with consistent totals language and recursive directory splits", () => {
    // A workspace name ending in Tests must not classify its production sources as tests.
    const root = path.resolve("SampleAppTests");
    const report = buildCodeCountReport({
      workspaceRoot: root,
      generatedAt: new Date("2026-10-12T00:00:00Z"),
      targetUri: vscode.Uri.file(root),
      reportUri: vscode.Uri.file(path.join(root, ".freecm/counts/results.md")),
      files: [
        ["src/main.cpp", "C++", 10, 2, 1],
        ["src/main_test.cpp", "C++", 4, 3, 2],
        ["Tests/Unit/helper.swift", "Swift", 6, 1, 3],
      ].map(([filename, language, code, comment, blank]) => ({
        uri: vscode.Uri.file(path.join(root, filename as string)),
        filename: path.join(root, filename as string), language: language as string,
        code: code as number, comment: comment as number, blank: blank as number,
      })),
    });
    assert.deepStrictEqual(report.production, { name: "Production", files: 1, code: 10, comment: 2, blank: 1, total: 13 });
    assert.deepStrictEqual(report.test, { name: "Test", files: 2, code: 10, comment: 4, blank: 5, total: 19 });
    for (const field of ["files", "code", "comment", "blank", "total"] as const) {
      assert.strictEqual(report.total[field], report.production[field] + report.test[field], field);
    }
    assert.deepStrictEqual(report.languages.map((item) => [item.name, item.productionCode, item.testCode]), [
      ["C++", 10, 4], ["Swift", 0, 6],
    ]);
    assert.deepStrictEqual(report.directories.map((item) => [item.name, item.productionCode, item.testCode]), [
      [".", 10, 10], ["Tests", 0, 6], ["Tests/Unit", 0, 6], ["src", 10, 4],
    ]);
    assert.ok(report.markdown.includes("| Production | 1 | 10 | 2 | 1 | 13 |"));
    assert.ok(report.markdown.includes("| Test | 2 | 10 | 4 | 5 | 19 |"));
    assert.ok(report.markdown.includes("| C++ | 2 | 14 | 5 | 3 | 22 | 10 | 4 |"));
    assert.ok(report.markdown.includes("| src/main_test.cpp | Test | C++ |"));
    assert.ok(report.markdown.includes("| src/main.cpp | Production | C++ |"));
    assert.ok(report.markdown.includes("## Classification Rules"));
  });

  test("keeps ancestor test context and reclassifies cached counts after overrides change", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "freecm-classification-"));
    const targetPath = path.join(workspaceRoot, "SampleAppTests", "Helpers");
    const outputRoot = path.join(workspaceRoot, ".freecm/counts");
    try {
      await fs.mkdir(targetPath, { recursive: true });
      await fs.writeFile(path.join(targetPath, "helper.cpp"), "int helper = 1;\n");
      const options = { workspaceRoot, targetPath, outputRoot, extensions: [] };
      const first = await countCode(options);
      assert.strictEqual(first.production.files, 0);
      assert.strictEqual(first.test.files, 1);
      const second = await countCode({ ...options, productionPaths: ["SampleAppTests/Helpers"] });
      assert.strictEqual(second.production.files, 1);
      assert.strictEqual(second.test.files, 0);
      assert.strictEqual(second.total.code, first.total.code);
      assert.deepStrictEqual(second.productionPaths, ["SampleAppTests/Helpers"]);
      assert.ok(second.markdown.includes("Explicit production paths: SampleAppTests/Helpers"));
      await assert.rejects(countCode({ ...options, testPaths: ["../outside"] }), /Invalid.*testPaths/);
    } finally {
      clearCodeCountFileCache();
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });
});
