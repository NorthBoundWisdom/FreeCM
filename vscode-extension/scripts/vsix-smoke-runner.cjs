const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vscode = require("vscode");

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
  const relativeToExtensions = path.relative(extensionsRoot, extensionRoot);
  assert.ok(
    relativeToExtensions &&
      !relativeToExtensions.startsWith(`..${path.sep}`) &&
      !path.isAbsolute(relativeToExtensions),
    `FreeCM loaded outside isolated extensions dir: ${extensionRoot}`,
  );
  for (const forbiddenRoot of [checkoutRoot, harnessRoot]) {
    const relative = path.relative(forbiddenRoot, extensionRoot);
    assert.ok(
      relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative),
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

  assert.equal(extension.isActive, false, "installed extension activated before smoke trigger");
  await extension.activate();
  assert.equal(extension.isActive, true, "installed extension did not activate");
  const commands = await vscode.commands.getCommands(true);
  for (const command of [
    "freecm.showWorkflowPanel",
    "freecm.init",
    "freecm.update",
    "freecm.build",
    "freecm.test",
  ]) {
    assert.ok(commands.includes(command), `installed extension did not register ${command}`);
  }
};
