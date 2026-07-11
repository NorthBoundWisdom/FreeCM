import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { beginFilesystemRead } from "../performanceMetrics";
import { discoverSourceCandidates, SourceCandidate } from "./fileDiscovery";
import { createLineCounterTable } from "./languageDiscovery";
import { buildCodeCountReport } from "./report";
import {
  DEFAULT_CODE_COUNT_EXCLUDE_PATHS,
  DEFAULT_MAX_FILE_BYTES,
  DEFAULT_MAX_FILES,
  DEFAULT_REPORT_RETENTION,
  isPathInside,
  normalizeCodeCountExcludePaths,
} from "./settings";
import {
  CodeCountFileResult,
  CodeCountReport,
  CodeCountSkippedFile,
  CountCodeOptions,
} from "./types";

const DEFAULT_ENCODING: BufferEncoding = "utf8";
const fileCountCache = new Map<string, {
  readonly size: number;
  readonly mtime: number;
  readonly languageVersion: string;
  readonly countOptions: string;
  readonly result: CodeCountFileResult;
}>();

export async function countCode(options: CountCodeOptions): Promise<CodeCountReport> {
  const workspaceRoot = path.resolve(options.workspaceRoot);
  const targetPath = path.resolve(options.targetPath);
  const outputRoot = path.resolve(options.outputRoot);
  ensureInside(workspaceRoot, targetPath, "Code count target");
  ensureInside(workspaceRoot, outputRoot, "Code count output");
  throwIfCancelled(options.cancellationToken);

  const finishTargetStat = beginFilesystemRead();
  try {
    if (!(await fs.stat(targetPath)).isDirectory()) {
      throw new Error(`Code count target is not a directory: ${targetPath}`);
    }
  } finally {
    finishTargetStat();
  }

  options.progress?.("Loading language definitions");
  const table = await createLineCounterTable(
    options.extensions ?? vscode.extensions.all,
    options.filesAssociations,
  );
  const excludePaths = normalizeCodeCountExcludePaths(
    options.excludePaths ?? DEFAULT_CODE_COUNT_EXCLUDE_PATHS,
  );
  options.progress?.("Finding supported source files");
  const candidates = await discoverSourceCandidates({
    workspaceRoot,
    targetPath,
    outputRoot,
    table,
    excludePaths,
    maxFiles: positiveInteger(options.maxFiles, DEFAULT_MAX_FILES, "maxFiles"),
    cancellationToken: options.cancellationToken,
  });
  throwIfCancelled(options.cancellationToken);

  options.progress?.(`Counting ${candidates.length} files`);
  const counted = await countFiles(candidates, {
    encoding: options.encoding ?? DEFAULT_ENCODING,
    includeIncompleteLine: options.includeIncompleteLine ?? true,
    maxFileBytes: positiveInteger(options.maxFileBytes, DEFAULT_MAX_FILE_BYTES, "maxFileBytes"),
    concurrency: adaptiveConcurrency(options.maxConcurrentReads, candidates.length),
    languageVersion: table.version,
    cancellationToken: options.cancellationToken,
    progress: options.progress,
  });
  throwIfCancelled(options.cancellationToken);
  if (counted.files.length === 0) {
    const details = counted.skipped.length === 0 ? "" : ` (${counted.skipped.length} skipped)`;
    throw new Error(`No supported source files were counted${details}.`);
  }

  const generatedAt = new Date();
  const reportDirectory = vscode.Uri.file(path.join(outputRoot, timestampForPath(generatedAt)));
  const warnings = counted.skipped.length === 0
    ? []
    : [`Skipped ${counted.skipped.length} unreadable, binary, or oversized file(s).`];
  const report = buildCodeCountReport({
    generatedAt,
    targetUri: vscode.Uri.file(targetPath),
    reportUri: vscode.Uri.joinPath(reportDirectory, "results.md"),
    files: counted.files,
    skippedFiles: counted.skipped,
    warnings,
    excludePaths,
  });
  throwIfCancelled(options.cancellationToken);
  await vscode.workspace.fs.createDirectory(reportDirectory);
  throwIfCancelled(options.cancellationToken);
  await vscode.workspace.fs.writeFile(report.reportUri, Buffer.from(report.markdown, "utf8"));
  await vscode.workspace.fs.writeFile(
    vscode.Uri.joinPath(reportDirectory, ".freecm-code-count-report"),
    Buffer.from("1\n", "utf8"),
  );
  await retainRecentReports(
    vscode.Uri.file(outputRoot),
    reportDirectory,
    positiveInteger(options.reportRetention, DEFAULT_REPORT_RETENTION, "reportRetention"),
  );
  return report;
}

export function clearCodeCountFileCache(): void {
  fileCountCache.clear();
}

async function countFiles(
  candidates: readonly SourceCandidate[],
  options: {
    readonly encoding: BufferEncoding;
    readonly includeIncompleteLine: boolean;
    readonly maxFileBytes: number;
    readonly concurrency: number;
    readonly languageVersion: string;
    readonly cancellationToken?: vscode.CancellationToken;
    readonly progress?: (message: string) => void;
  },
): Promise<{ readonly files: CodeCountFileResult[]; readonly skipped: CodeCountSkippedFile[] }> {
  const files: CodeCountFileResult[] = [];
  const skipped: CodeCountSkippedFile[] = [];
  let nextIndex = 0;
  let completed = 0;
  async function worker(): Promise<void> {
    while (nextIndex < candidates.length) {
      throwIfCancelled(options.cancellationToken);
      const candidate = candidates[nextIndex];
      nextIndex += 1;
      const result = await countCandidate(candidate, options);
      if ("reason" in result) skipped.push(result);
      else files.push(result);
      completed += 1;
      if (completed % 100 === 0 || completed === candidates.length) {
        options.progress?.(`Counting ${completed}/${candidates.length}`);
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(options.concurrency, Math.max(1, candidates.length)) }, worker));
  return { files, skipped };
}

async function countCandidate(
  candidate: SourceCandidate,
  options: {
    readonly encoding: BufferEncoding;
    readonly includeIncompleteLine: boolean;
    readonly maxFileBytes: number;
    readonly languageVersion: string;
    readonly cancellationToken?: vscode.CancellationToken;
  },
): Promise<CodeCountFileResult | CodeCountSkippedFile> {
  const finishStat = beginFilesystemRead();
  let stat: vscode.FileStat;
  try {
    stat = await vscode.workspace.fs.stat(candidate.uri);
  } catch {
    return { filename: candidate.uri.fsPath, reason: "unreadable" };
  } finally {
    finishStat();
  }
  if (stat.size > options.maxFileBytes) {
    return { filename: candidate.uri.fsPath, reason: "large" };
  }
  const cached = fileCountCache.get(candidate.uri.fsPath);
  if (
    cached?.size === stat.size &&
    cached.mtime === stat.mtime &&
    cached.languageVersion === options.languageVersion &&
    cached.countOptions === `${options.encoding}:${options.includeIncompleteLine}`
  ) {
    return cached.result;
  }
  throwIfCancelled(options.cancellationToken);
  const finishRead = beginFilesystemRead();
  let data: Uint8Array;
  try {
    data = await vscode.workspace.fs.readFile(candidate.uri);
  } catch {
    return { filename: candidate.uri.fsPath, reason: "unreadable" };
  } finally {
    finishRead();
  }
  if (looksBinary(data)) return { filename: candidate.uri.fsPath, reason: "binary" };
  const count = candidate.counter.count(
    Buffer.from(data).toString(options.encoding),
    options.includeIncompleteLine,
  );
  const result: CodeCountFileResult = {
    uri: candidate.uri,
    filename: candidate.uri.fsPath,
    language: candidate.counter.name,
    code: count.code,
    comment: count.comment,
    blank: count.blank,
  };
  fileCountCache.set(candidate.uri.fsPath, {
    size: stat.size,
    mtime: stat.mtime,
    languageVersion: options.languageVersion,
    countOptions: `${options.encoding}:${options.includeIncompleteLine}`,
    result,
  });
  return result;
}

async function retainRecentReports(
  outputRoot: vscode.Uri,
  current: vscode.Uri,
  retention: number,
): Promise<void> {
  let entries: [string, vscode.FileType][];
  const finishRead = beginFilesystemRead();
  try {
    entries = await vscode.workspace.fs.readDirectory(outputRoot);
  } catch {
    return;
  } finally {
    finishRead();
  }
  const reports = entries
    .filter(([name, type]) => type === vscode.FileType.Directory && /^\d{8}_\d{6}$/.test(name))
    .map(([name]) => name)
    .sort()
    .reverse();
  const currentName = path.basename(current.fsPath);
  const managedReports: string[] = [];
  for (const name of reports) {
    if (name === currentName || await isManagedReportDirectory(vscode.Uri.joinPath(outputRoot, name))) {
      managedReports.push(name);
    }
  }
  const retained = new Set([currentName, ...managedReports.filter((name) => name !== currentName).slice(0, retention - 1)]);
  for (const name of managedReports) {
    if (!retained.has(name)) {
      await vscode.workspace.fs.delete(vscode.Uri.joinPath(outputRoot, name), { recursive: true, useTrash: false });
    }
  }
}

async function isManagedReportDirectory(directory: vscode.Uri): Promise<boolean> {
  try {
    const reportUri = vscode.Uri.joinPath(directory, "results.md");
    const report = await measuredStat(reportUri);
    if (report.type !== vscode.FileType.File) return false;
    try {
      const marker = await measuredStat(vscode.Uri.joinPath(directory, ".freecm-code-count-report"));
      if (marker.type === vscode.FileType.File) return true;
    } catch {
      // Pre-marker reports are recognized only by the exact generated header.
    }
    const content = await measuredReadFile(reportUri);
    return Buffer.from(content).subarray(0, 64).toString("utf8").startsWith("# FreeCM Code Count\n");
  } catch {
    return false;
  }
}

async function measuredStat(uri: vscode.Uri): Promise<vscode.FileStat> {
  const finish = beginFilesystemRead();
  try {
    return await vscode.workspace.fs.stat(uri);
  } finally {
    finish();
  }
}

async function measuredReadFile(uri: vscode.Uri): Promise<Uint8Array> {
  const finish = beginFilesystemRead();
  try {
    return await vscode.workspace.fs.readFile(uri);
  } finally {
    finish();
  }
}

function adaptiveConcurrency(requested: number | undefined, fileCount: number): number {
  const fallback = Math.max(2, Math.min(16, os.availableParallelism() * 2));
  return Math.max(1, Math.min(positiveInteger(requested, fallback, "maxConcurrentReads"), Math.max(1, fileCount)));
}

function positiveInteger(value: number | undefined, fallback: number, label: string): number {
  const selected = value ?? fallback;
  if (!Number.isInteger(selected) || selected <= 0) throw new Error(`${label} must be a positive integer.`);
  return selected;
}

function ensureInside(parent: string, child: string, label: string): void {
  if (!isPathInside(parent, child)) throw new Error(`${label} must be inside ${parent}: ${child}`);
}

function looksBinary(data: Uint8Array): boolean {
  for (let index = 0; index < Math.min(data.length, 4096); index += 1) {
    if (data[index] === 0) return true;
  }
  return false;
}

function throwIfCancelled(token: vscode.CancellationToken | undefined): void {
  if (token?.isCancellationRequested) throw new vscode.CancellationError();
}

function timestampForPath(date: Date): string {
  const pad = (value: number) => value.toString().padStart(2, "0");
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
}
