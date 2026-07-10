import * as assert from "assert";
import * as fs from "fs";
import * as path from "path";
import {
  lockCoreProjection,
  parseLockText,
} from "../../lockSchemaValidation";

interface ConformanceFixture {
  readonly name: string;
  readonly file: string;
  readonly valid: boolean;
  readonly roundTrip?: boolean;
  readonly expected?: unknown;
}

interface ConformanceManifest {
  readonly cases: readonly ConformanceFixture[];
}

const fixtureRoot = path.resolve(
  __dirname,
  "../../../../tests/fixtures/dependency-lock-conformance",
);

suite("lock schema conformance", () => {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(fixtureRoot, "manifest.json"), "utf8"),
  ) as ConformanceManifest;

  for (const fixture of manifest.cases) {
    test(fixture.name, () => {
      const fixturePath = path.join(fixtureRoot, fixture.file);
      const text = fs.readFileSync(fixturePath, "utf8");
      if (!fixture.valid) {
        assert.throws(() => parseLockText(text, fixturePath));
        return;
      }

      const projection = lockCoreProjection(
        parseLockText(text, fixturePath),
        fixturePath,
      );
      assert.deepStrictEqual(projection, fixture.expected);
      if (fixture.roundTrip) {
        const roundTripText = JSON.stringify(projection, null, 2);
        assert.deepStrictEqual(
          lockCoreProjection(
            parseLockText(roundTripText, `round-trip:${fixture.name}`),
            `round-trip:${fixture.name}`,
          ),
          fixture.expected,
        );
      }
    });
  }
});
