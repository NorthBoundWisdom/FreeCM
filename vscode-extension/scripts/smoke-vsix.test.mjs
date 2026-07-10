import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import JSZip from "jszip";
import { inspectVsix } from "./smoke-vsix.mjs";

const expectedPackage = {
  name: "freecm",
  publisher: "ethan-kang",
  version: "1.2.3",
  main: "./out/extension.js",
  engines: { vscode: "^1.90.0" },
};

async function writeFixture(mutator) {
  const root = await mkdtemp(path.join(tmpdir(), "freecm-vsix-inspect-"));
  const vsixPath = path.join(root, "fixture.vsix");
  const zip = new JSZip();
  zip.file("[Content_Types].xml", "types");
  zip.file(
    "extension.vsixmanifest",
    '<Identity Id="freecm" Version="1.2.3" Publisher="ethan-kang" />' +
      '<Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.90.0" />',
  );
  zip.file("extension/package.json", JSON.stringify(expectedPackage));
  zip.file("extension/out/extension.js", "exports.activate = () => {};");
  zip.file("extension/resources/freecm.svg", "svg");
  zip.file("extension/resources/freecm-icon.png", "png");
  zip.file("extension/resources/workflow.css", "css");
  zip.file("extension/resources/workflow.js", "js");
  zip.file("extension/node_modules/jsonc-parser/package.json", "{}");
  await mutator?.(zip);
  await writeFile(vsixPath, await zip.generateAsync({ type: "nodebuffer" }));
  return { root, vsixPath };
}

async function withFixture(mutator, callback) {
  const fixture = await writeFixture(mutator);
  try {
    await callback(fixture.vsixPath);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
}

test("inspectVsix accepts a valid packaged extension", async () => {
  await withFixture(undefined, async (vsixPath) => {
    await inspectVsix(vsixPath, expectedPackage, expectedPackage.version);
  });
});

test("inspectVsix rejects mismatched metadata", async () => {
  await withFixture(
    (zip) => zip.file("extension/package.json", JSON.stringify({ ...expectedPackage, version: "9" })),
    async (vsixPath) => {
      await assert.rejects(
        inspectVsix(vsixPath, expectedPackage, expectedPackage.version),
        /version mismatch/,
      );
    },
  );
});

test("inspectVsix rejects mismatched VSIX identity metadata", async () => {
  await withFixture(
    (zip) =>
      zip.file(
        "extension.vsixmanifest",
        '<Identity Id="freecm" Version="1.2.3" Publisher="wrong" />' +
          '<Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.90.0" />',
      ),
    async (vsixPath) => {
      await assert.rejects(
        inspectVsix(vsixPath, expectedPackage, expectedPackage.version),
        /Publisher/,
      );
    },
  );
});

test("inspectVsix rejects unsafe archive paths", async () => {
  await withFixture(
    (zip) => zip.file("../escape.txt", "escape"),
    async (vsixPath) => {
      await assert.rejects(inspectVsix(vsixPath, expectedPackage, expectedPackage.version), /unsafe/);
    },
  );
});

test("inspectVsix rejects a missing compiled main", async () => {
  await withFixture(
    (zip) => zip.remove("extension/out/extension.js"),
    async (vsixPath) => {
      await assert.rejects(inspectVsix(vsixPath, expectedPackage, expectedPackage.version), /missing/);
    },
  );
});
