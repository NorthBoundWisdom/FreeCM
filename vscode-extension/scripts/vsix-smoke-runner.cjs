const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vscode = require("vscode");
const { isPathWithin, isSamePath } = require("./vsix-smoke-paths.cjs");

exports.run = async function run() {
  const expectedVersion = process.env.FREECM_SMOKE_EXPECTED_VERSION;
  const extensionsRoot = fs.realpathSync(
    process.env.FREECM_SMOKE_EXTENSIONS_DIR,
  );
  const checkoutRoot = fs.realpathSync(process.env.FREECM_SMOKE_CHECKOUT_ROOT);
  const harnessRoot = fs.realpathSync(process.env.FREECM_SMOKE_HARNESS_ROOT);
  assert.ok(expectedVersion, "expected version must be provided");

  const extension = vscode.extensions.getExtension("ethan-kang.freecm");
  assert.ok(extension, "installed ethan-kang.freecm extension was not discovered");
  const extensionRoot = fs.realpathSync(extension.extensionPath);
  assert.ok(
    !isSamePath(extensionsRoot, extensionRoot) &&
      isPathWithin(extensionsRoot, extensionRoot),
    `FreeCM loaded outside isolated extensions dir: ${extensionRoot}`,
  );
  for (const forbiddenRoot of [checkoutRoot, harnessRoot]) {
    assert.ok(
      !isPathWithin(forbiddenRoot, extensionRoot),
      `FreeCM loaded from development path: ${extensionRoot}`,
    );
  }

  assert.equal(extension.packageJSON.name, "freecm");
  assert.equal(extension.packageJSON.publisher, "ethan-kang");
  assert.equal(extension.packageJSON.version, expectedVersion);
  assert.equal(extension.packageJSON.main, "./out/extension.js");
  assert.ok(
    fs.statSync(path.join(extensionRoot, "out", "extension.js")).isFile(),
    "installed extension main file is missing",
  );
  const codeCountConfiguration = vscode.workspace.getConfiguration("freecm.codeCount");
  assert.equal(codeCountConfiguration.get("maxConcurrentReads"), null);

  assert.equal(extension.isActive, false, "installed extension activated before smoke trigger");
  await extension.activate();
  assert.equal(extension.isActive, true, "installed extension did not activate");
  const commands = await vscode.commands.getCommands(true);
  for (const command of [
    "freecm.showWorkflowPanel",
    "freecm.init",
    "freecm.update",
    "freecm.pullSeeds",
    "freecm.countCode",
    "freecm.build",
    "freecm.test",
  ]) {
    assert.ok(commands.includes(command), `installed extension did not register ${command}`);
  }
  assert.ok(
    !commands.includes("freecm.pullFreeCM"),
    "installed extension retained removed freecm.pullFreeCM command",
  );

  const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
  assert.ok(workspaceFolder, "VSIX smoke workspace was not opened");
  fs.writeFileSync(path.join(workspaceFolder.uri.fsPath, "main.rs"), "fn main() {}\n");
  await vscode.commands.executeCommand("freecm.countCode");
  const reportRoot = path.join(workspaceFolder.uri.fsPath, ".freecm", "counts");
  const reports = fs.readdirSync(reportRoot).filter((name) => /^\d{8}_\d{6}$/.test(name));
  assert.equal(reports.length, 1, "installed code count did not create one report");
  assert.match(
    fs.readFileSync(path.join(reportRoot, reports[0], "results.md"), "utf8"),
    /^# FreeCM Code Count\n/,
  );
};
