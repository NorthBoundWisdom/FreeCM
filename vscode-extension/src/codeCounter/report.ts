import * as path from "path";
import * as vscode from "vscode";
import {
  CODE_COUNT_CLASSIFICATION_DESCRIPTION,
  createCodeCountClassifier,
  normalizeClassificationPaths,
} from "./classification";
import { EXCLUDED_CODE_COUNT_FORMATS } from "./fileDiscovery";
import { Count } from "./lineCounter";
import { normalizeCodeCountExcludePaths, normalizeRelativePath } from "./settings";
import {
  CodeCountFileResult,
  ClassifiedCodeCountFileResult,
  CodeCountReport,
  CodeCountSkippedFile,
  CodeCountStatistics,
  CodeCountSplitStatistics,
} from "./types";

export function buildCodeCountReport(input: {
  readonly workspaceRoot: string;
  readonly generatedAt: Date;
  readonly targetUri: vscode.Uri;
  readonly reportUri: vscode.Uri;
  readonly files: readonly CodeCountFileResult[];
  readonly skippedFiles?: readonly CodeCountSkippedFile[];
  readonly warnings?: readonly string[];
  readonly excludePaths?: readonly string[];
  readonly testPaths?: readonly string[];
  readonly productionPaths?: readonly string[];
}): CodeCountReport {
  const languages = new Map<string, MutableStatistics>();
  const directories = new Map<string, MutableStatistics>();
  const total = new MutableStatistics("Total");
  const production = new MutableStatistics("Production");
  const test = new MutableStatistics("Test");
  const classify = createCodeCountClassifier(input);
  const files = input.files.map((file): ClassifiedCodeCountFileResult => ({
    ...file,
    category: classify(path.relative(input.workspaceRoot, file.filename)),
  }));
  for (const file of files) {
    total.addFile(file);
    (file.category === "test" ? test : production).addFile(file);
    getOrCreate(languages, file.language, () => new MutableStatistics(file.language)).addFile(file);
    const relative = normalizeRelativePath(path.relative(input.targetUri.fsPath, file.filename));
    for (const directory of directoryPathsForFile(relative)) {
      getOrCreate(directories, directory, () => new MutableStatistics(directory)).addFile(file);
    }
  }
  const report = {
    generatedAt: input.generatedAt,
    targetUri: input.targetUri,
    reportUri: input.reportUri,
    files: files.sort((a, b) => stringCompare(a.filename, b.filename)),
    skippedFiles: [...(input.skippedFiles ?? [])].sort((a, b) => stringCompare(a.filename, b.filename)),
    warnings: [...(input.warnings ?? [])],
    total: total.snapshot(),
    production: production.snapshot(),
    test: test.snapshot(),
    languages: [...languages.values()].map((item) => item.splitSnapshot()).sort((a, b) => b.code - a.code || stringCompare(a.name, b.name)),
    directories: [...directories.values()].map((item) => item.splitSnapshot()).sort((a, b) => directoryTreeCompare(a.name, b.name)),
    excludedPaths: normalizeCodeCountExcludePaths(input.excludePaths ?? []),
    testPaths: normalizeClassificationPaths(input.testPaths ?? [], "testPaths"),
    productionPaths: normalizeClassificationPaths(input.productionPaths ?? [], "productionPaths"),
  };
  return { ...report, markdown: codeCountMarkdown(report) };
}

function codeCountMarkdown(report: Omit<CodeCountReport, "markdown">): string {
  const lines = [
    "# FreeCM Code Count", "", `Date: ${formatDate(report.generatedAt)}`,
    `Directory: ${report.targetUri.fsPath}`,
    `Total: ${formatNumber(report.total.files)} files, ${formatNumber(report.total.code)} code, ${formatNumber(report.total.comment)} comments, ${formatNumber(report.total.blank)} blanks, ${formatNumber(report.total.total)} lines`,
    "", "## Production And Test", "",
    statisticsTable([report.production, report.test, report.total], "category"),
    "", "## Languages", "", splitStatisticsTable(report.languages, "language"),
    "", "## Directories", "", directoryStatisticsTable(report.directories),
    "", "## Files", "",
    "| file | category | language | code | comment | blank | total |",
    "| :--- | :--- | :--- | ---: | ---: | ---: | ---: |",
    ...report.files.map((file) => {
      const relative = normalizeRelativePath(path.relative(report.targetUri.fsPath, file.filename)) || path.basename(file.filename);
      return `| ${markdownCell(relative)} | ${file.category === "test" ? "Test" : "Production"} | ${markdownCell(file.language)} | ${formatNumber(file.code)} | ${formatNumber(file.comment)} | ${formatNumber(file.blank)} | ${formatNumber(file.code + file.comment + file.blank)} |`;
    }),
    "", "## Classification Rules", "", CODE_COUNT_CLASSIFICATION_DESCRIPTION,
    "", `Explicit production paths: ${report.productionPaths.map(markdownCell).join(", ") || "None"}`,
    `Explicit test paths: ${report.testPaths.map(markdownCell).join(", ") || "None"}`,
    "", "## Skipped Files And Warnings", "",
    ...(report.warnings.length === 0 && report.skippedFiles.length === 0
      ? ["None."]
      : [
          ...report.warnings.map((warning) => `- Warning: ${warning}`),
          ...report.skippedFiles.map((file) => `- ${markdownCell(normalizeRelativePath(path.relative(report.targetUri.fsPath, file.filename)))}: ${file.reason}`),
        ]),
    "", "## Excluded Formats And Paths", "",
    ...EXCLUDED_CODE_COUNT_FORMATS.map((format) => `- ${format}`),
    ...report.excludedPaths.map((excluded) => `- ${excluded}`), "",
  ];
  return lines.join("\n");
}

function statisticsTable(values: readonly CodeCountStatistics[], name: string): string {
  return [
    `| ${name} | files | code | comment | blank | total |`,
    "| :--- | ---: | ---: | ---: | ---: | ---: |",
    ...values.map((item) => `| ${markdownCell(item.name)} | ${formatNumber(item.files)} | ${formatNumber(item.code)} | ${formatNumber(item.comment)} | ${formatNumber(item.blank)} | ${formatNumber(item.total)} |`),
  ].join("\n");
}

function splitStatisticsTable(values: readonly CodeCountSplitStatistics[], name: string): string {
  return [
    `| ${name} | files | code | comment | blank | total | production code | test code |`,
    "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...values.map((item) => `| ${markdownCell(item.name)} | ${formatNumber(item.files)} | ${formatNumber(item.code)} | ${formatNumber(item.comment)} | ${formatNumber(item.blank)} | ${formatNumber(item.total)} | ${formatNumber(item.productionCode)} | ${formatNumber(item.testCode)} |`),
  ].join("\n");
}

function directoryStatisticsTable(values: readonly CodeCountSplitStatistics[]): string {
  return splitStatisticsTable(values.map((item) => ({ ...item, name: displayDirectoryName(item.name) })), "directory");
}

function directoryPathsForFile(relativeFilePath: string): string[] {
  const directory = path.posix.dirname(normalizeRelativePath(relativeFilePath));
  if (directory === ".") return ["."];
  const values = ["."];
  let current = "";
  for (const part of directory.split("/")) {
    current = current === "" ? part : `${current}/${part}`;
    values.push(current);
  }
  return values;
}

function directoryTreeCompare(left: string, right: string): number {
  const leftParts = left === "." ? [] : left.split("/");
  const rightParts = right === "." ? [] : right.split("/");
  for (let index = 0; index < Math.min(leftParts.length, rightParts.length); index += 1) {
    const compared = stringCompare(leftParts[index], rightParts[index]);
    if (compared !== 0) return compared;
  }
  return leftParts.length - rightParts.length;
}

function displayDirectoryName(directory: string): string {
  if (directory === ".") return ".";
  const depth = directory.split("/").length - 1;
  return `${"&nbsp;&nbsp;".repeat(depth)}${path.posix.basename(directory)}`;
}

function formatDate(date: Date): string {
  return date.toISOString();
}

function formatNumber(value: number): string {
  return value.toLocaleString("en-US");
}

function markdownCell(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

function stringCompare(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function getOrCreate<K, V>(map: Map<K, V>, key: K, create: () => V): V {
  const existing = map.get(key);
  if (existing !== undefined) return existing;
  const value = create();
  map.set(key, value);
  return value;
}

class MutableStatistics extends Count {
  files = 0;
  productionCode = 0;
  testCode = 0;
  constructor(readonly name: string) { super(); }
  addFile(value: ClassifiedCodeCountFileResult): void {
    this.files += 1;
    this.add(value);
    if (value.category === "test") this.testCode += value.code;
    else this.productionCode += value.code;
  }
  snapshot(): CodeCountStatistics {
    return { name: this.name, files: this.files, code: this.code, comment: this.comment, blank: this.blank, total: this.total };
  }
  splitSnapshot(): CodeCountSplitStatistics {
    return { ...this.snapshot(), productionCode: this.productionCode, testCode: this.testCode };
  }
}
