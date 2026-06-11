import * as assert from "assert";
import {
  PRIMARY_REPO_COMMAND_ACTIONS,
  isRepoCommandAction,
  isRepoCommandSelectCommand,
  repoCommandActionForSelectCommand,
  statusBarIconForRepoAction,
  titleCase,
} from "../../commands/repoCommandActions";

suite("repo command actions", () => {
  test("classifies panel commands and maps selectors to actions", () => {
    assert.strictEqual(isRepoCommandAction("build"), true);
    assert.strictEqual(isRepoCommandAction("selectBuild"), false);
    assert.strictEqual(isRepoCommandSelectCommand("selectPackage"), true);
    assert.strictEqual(isRepoCommandSelectCommand("package"), false);
    assert.strictEqual(
      repoCommandActionForSelectCommand("selectConfig"),
      "config",
    );
    assert.strictEqual(
      repoCommandActionForSelectCommand("selectBuild"),
      "build",
    );
    assert.strictEqual(repoCommandActionForSelectCommand("selectTest"), "test");
    assert.strictEqual(repoCommandActionForSelectCommand("selectRun"), "run");
    assert.strictEqual(
      repoCommandActionForSelectCommand("selectPackage"),
      "package",
    );
  });

  test("provides status bar labels and icons", () => {
    assert.deepStrictEqual(PRIMARY_REPO_COMMAND_ACTIONS, [
      "config",
      "build",
      "run",
    ]);
    assert.strictEqual(titleCase("config"), "Config");
    assert.strictEqual(statusBarIconForRepoAction("config"), "$(gear)");
    assert.strictEqual(statusBarIconForRepoAction("build"), "$(tools)");
    assert.strictEqual(statusBarIconForRepoAction("test"), "$(beaker)");
    assert.strictEqual(statusBarIconForRepoAction("run"), "$(play)");
    assert.strictEqual(statusBarIconForRepoAction("package"), "$(package)");
  });
});
