import {
  codeCountExcludePathError,
  normalizeCodeCountExcludePaths,
  normalizeRelativePath,
} from "./settings";
import { CodeCountCategory } from "./types";

export interface CodeCountClassificationPaths {
  readonly testPaths?: readonly string[];
  readonly productionPaths?: readonly string[];
}

export const CODE_COUNT_CLASSIFICATION_DESCRIPTION =
  "Path-based classification uses workspace-relative paths, even when counting a subdirectory. " +
  "Test directories include test/tests, spec/specs, __tests__, __mocks__, testing, e2e, " +
  "test_package, Android androidTest/testFixtures, and targets ending in Test/Tests or Spec/Specs " +
  "(including UITests). Test filenames include test_*, *_test, *.test.*, *.spec.*, " +
  "*Test/*Tests, *Spec/*Specs, and conftest.py. Matching uses name boundaries, not arbitrary substrings. " +
  "Explicit production paths take precedence over explicit test paths and automatic rules. " +
  "Other counted files are classified as production, including tools and build scripts. " +
  "Files containing both production and inline tests are counted as a whole; source contents are not parsed for test frameworks.";

const TEST_DIRECTORIES = new Set([
  "test", "tests", "spec", "specs", "__tests__", "__mocks__", "testing", "e2e",
  "androidtest", "androidunittest", "testfixtures", "test_package",
]);
const TEST_TOKEN = /(?:^|[._-])(?:tests?|specs?)(?:$|[._-])/i;
// Keep camel-case boundaries: Contest, Latest and TestFlight are not test names.
const TEST_TARGET_SUFFIX = /[A-Za-z0-9](?:Tests?|Specs?)$/;

export function normalizeClassificationPaths(
  values: readonly string[],
  setting: "testPaths" | "productionPaths",
): string[] {
  for (const value of values) {
    const error = codeCountExcludePathError(value);
    if (error !== undefined) {
      throw new Error(`Invalid freecm.codeCount.${setting} path ${JSON.stringify(value)}: ${error}`);
    }
  }
  return normalizeCodeCountExcludePaths(values);
}

export function createCodeCountClassifier(
  options: CodeCountClassificationPaths = {},
): (workspaceRelativePath: string) => CodeCountCategory {
  const production = normalizeClassificationPaths(options.productionPaths ?? [], "productionPaths")
    .map((value) => value.toLowerCase());
  const tests = normalizeClassificationPaths(options.testPaths ?? [], "testPaths")
    .map((value) => value.toLowerCase());
  return (workspaceRelativePath) => {
    const relative = normalizeRelativePath(workspaceRelativePath);
    const lower = relative.toLowerCase();
    const matches = (values: readonly string[]) =>
      values.some((value) => lower === value || lower.startsWith(`${value}/`));
    if (matches(production)) return "production";
    if (matches(tests)) return "test";
    const parts = relative.split("/");
    const filename = parts.pop() ?? "";
    if (parts.some((part) => TEST_DIRECTORIES.has(part.toLowerCase()) ||
      TEST_TOKEN.test(part) || TEST_TARGET_SUFFIX.test(part))) return "test";
    const extension = filename.lastIndexOf(".");
    const stem = extension > 0 ? filename.slice(0, extension) : filename;
    return filename.toLowerCase() === "conftest.py" ||
      TEST_TOKEN.test(stem) || TEST_TARGET_SUFFIX.test(stem) ? "test" : "production";
  };
}
