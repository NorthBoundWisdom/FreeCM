import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import JSZip from "jszip";
import {
  MAX_ICON_BYTES,
  MAX_VSIX_COMPRESSED_BYTES,
  MAX_VSIX_UNPACKED_BYTES,
  inspectVsix,
} from "./smoke-vsix.mjs";

const require = createRequire(import.meta.url);
const { isPathWithin, isSamePath } = require("./vsix-smoke-paths.cjs");

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
  zip.file("extension/resources/freecm-icon.png", pngHeader(256, 256));
  zip.file("extension/resources/workflow.css", "css");
  zip.file("extension/resources/workflow.js", "js");
  zip.file("extension/node_modules/jsonc-parser/package.json", "{}");
  zip.file("extension/node_modules/ignore/package.json", "{}");
  zip.file("extension/node_modules/ignore/index.js", "module.exports = () => ({});");
  await mutator?.(zip);
  await writeFile(
    vsixPath,
    await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }),
  );
  return { root, vsixPath };
}

function pngHeader(width, height) {
  const header = Buffer.alloc(24);
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).copy(header);
  header.writeUInt32BE(13, 8);
  header.write("IHDR", 12, "ascii");
  header.writeUInt32BE(width, 16);
  header.writeUInt32BE(height, 20);
  return header;
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

test("VSIX smoke path checks use filesystem ancestry", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "freecm-vsix-paths-"));
  const extensionsRoot = path.join(root, "extensions");
  const extensionRoot = path.join(extensionsRoot, "ethan-kang.freecm-1.2.3");
  const outsideRoot = path.join(root, "outside");
  try {
    await Promise.all([
      mkdir(extensionRoot, { recursive: true }),
      mkdir(outsideRoot, { recursive: true }),
    ]);
    const canonicalExtensionsRoot = await realpath(extensionsRoot);
    assert.equal(isPathWithin(canonicalExtensionsRoot, extensionRoot), true);
    assert.equal(isPathWithin(extensionsRoot, extensionsRoot), true);
    assert.equal(isPathWithin(extensionsRoot, outsideRoot), false);
    assert.equal(isSamePath(canonicalExtensionsRoot, extensionsRoot), true);
    assert.equal(isSamePath(extensionsRoot, path.join(extensionsRoot, ".")), true);
    assert.equal(isSamePath(extensionsRoot, extensionRoot), false);
    if (process.platform === "win32" && extensionsRoot.includes("~")) {
      assert.notEqual(canonicalExtensionsRoot, extensionsRoot);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
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

test("inspectVsix enforces the archive allowlist", async () => {
  await withFixture(
    (zip) => zip.file("extension/unexpected.txt", "unexpected"),
    async (vsixPath) => {
      await assert.rejects(inspectVsix(vsixPath, expectedPackage, expectedPackage.version), /allowlist/);
    },
  );
});

test("inspectVsix enforces compressed and unpacked size budgets", async () => {
  await withFixture(
    (zip) => zip.file("extension/resources/freecm-icon.png", randomBytes(MAX_VSIX_COMPRESSED_BYTES + 1)),
    async (vsixPath) => {
      await assert.rejects(inspectVsix(vsixPath, expectedPackage, expectedPackage.version), /compressed size/);
    },
  );
  await withFixture(
    (zip) => zip.file("extension/resources/freecm-icon.png", Buffer.alloc(MAX_VSIX_UNPACKED_BYTES + 1)),
    async (vsixPath) => {
      await assert.rejects(inspectVsix(vsixPath, expectedPackage, expectedPackage.version), /unpacked size/);
    },
  );
});

test("inspectVsix enforces icon byte and dimension budgets", async () => {
  await withFixture(
    (zip) => zip.file("extension/resources/freecm-icon.png", pngHeader(512, 256)),
    async (vsixPath) => {
      await assert.rejects(inspectVsix(vsixPath, expectedPackage, expectedPackage.version), /dimensions/);
    },
  );
  assert.equal(MAX_ICON_BYTES, 100 * 1024);
});
