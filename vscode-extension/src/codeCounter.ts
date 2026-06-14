import * as fs from "fs/promises";
import * as path from "path";
import * as vscode from "vscode";

export interface LineCount {
  readonly code: number;
  readonly comment: number;
  readonly blank: number;
}

export class Count implements LineCount {
  constructor(
    public code = 0,
    public comment = 0,
    public blank = 0,
  ) {}

  get total(): number {
    return this.code + this.comment + this.blank;
  }

  get isEmpty(): boolean {
    return this.code === 0 && this.comment === 0 && this.blank === 0;
  }

  add(value: LineCount): Count {
    this.code += value.code;
    this.comment += value.comment;
    this.blank += value.blank;
    return this;
  }

  sub(value: LineCount): Count {
    this.code -= value.code;
    this.comment -= value.comment;
    this.blank -= value.blank;
    return this;
  }
}

export interface LanguageDefinition {
  readonly aliases?: readonly string[];
  readonly filenames?: readonly string[];
  readonly extensions?: readonly string[];
  readonly lineComments?: readonly string[];
  readonly blockComments?: readonly (readonly [string, string])[];
  readonly blockStrings?: readonly (readonly [string, string])[];
  readonly lineStrings?: readonly (readonly [string, string])[];
  readonly blockStringAsComment?: boolean;
}

export interface MutableLanguageDefinition {
  aliases: string[];
  filenames: string[];
  extensions: string[];
  lineComments: string[];
  blockComments: Array<[string, string]>;
  blockStrings: Array<[string, string]>;
  lineStrings: Array<[string, string]>;
  blockStringAsComment?: boolean;
}

export interface CodeCountFileResult extends LineCount {
  readonly uri: vscode.Uri;
  readonly filename: string;
  readonly language: string;
}

export interface CodeCountStatistics extends LineCount {
  readonly name: string;
  readonly files: number;
  readonly total: number;
}

export interface CodeCountReport {
  readonly generatedAt: Date;
  readonly targetUri: vscode.Uri;
  readonly reportUri: vscode.Uri;
  readonly files: readonly CodeCountFileResult[];
  readonly total: CodeCountStatistics;
  readonly languages: readonly CodeCountStatistics[];
  readonly directories: readonly CodeCountStatistics[];
  readonly excludedPaths: readonly string[];
  readonly markdown: string;
}

export interface CountCodeOptions {
  readonly workspaceRoot: string;
  readonly targetPath: string;
  readonly outputRoot: string;
  readonly extensions?: readonly vscode.Extension<unknown>[];
  readonly filesAssociations?: Readonly<Record<string, string>>;
  readonly encoding?: BufferEncoding;
  readonly maxFiles?: number;
  readonly maxConcurrentReads?: number;
  readonly includeIncompleteLine?: boolean;
  readonly excludePaths?: readonly string[];
  readonly progress?: (message: string) => void;
}

interface VscodeLanguageContribution {
  readonly id?: unknown;
  readonly aliases?: unknown;
  readonly filenames?: unknown;
  readonly extensions?: unknown;
  readonly configuration?: unknown;
}

interface VscodeLanguageConfiguration {
  readonly comments?: {
    readonly lineComment?: unknown;
    readonly blockComment?: unknown;
  };
  readonly brackets?: unknown;
  readonly autoClosingPairs?: unknown;
}

interface GitignoreDirectoryRule {
  readonly basePath: string;
  readonly path: string;
  readonly matchAnywhere: boolean;
}

const MAX_DEFAULT_FILES = 100_000;
const MAX_DEFAULT_CONCURRENT_READS = 64;
const DEFAULT_ENCODING: BufferEncoding = "utf8";
const EXCLUDED_CODE_COUNT_EXTENSION_LABELS: Readonly<Record<string, string>> = {
  ".bat": "Batch",
  ".cfg": "INI/config",
  ".cmd": "Batch",
  ".conf": "INI/config",
  ".config": "INI/config",
  ".css": "CSS/styles",
  ".gitignore": "Ignore",
  ".htm": "HTML",
  ".html": "HTML",
  ".ignore": "Ignore",
  ".ini": "INI/config",
  ".json": "JSON",
  ".json5": "JSON",
  ".jsonc": "JSON",
  ".markdown": "Markdown",
  ".md": "Markdown",
  ".plist": "XML",
  ".properties": "INI/config",
  ".sass": "CSS/styles",
  ".scss": "CSS/styles",
  ".less": "CSS/styles",
  ".rst": "reStructuredText",
  ".storyboard": "XML",
  ".svg": "XML",
  ".toml": "INI/config",
  ".xib": "XML",
  ".xml": "XML",
  ".yaml": "YAML",
  ".yml": "YAML",
};
const EXCLUDED_CODE_COUNT_EXTENSIONS = new Set(
  Object.keys(EXCLUDED_CODE_COUNT_EXTENSION_LABELS),
);
const EXCLUDED_CODE_COUNT_FILENAMES = new Set([
  ".dockerignore",
  ".eslintignore",
  ".gitignore",
  ".npmignore",
  "dockerignore",
  "eslintignore",
  "gitignore",
  "jsconfig.json",
  "npmignore",
  "pipfile",
  "requirements-dev.txt",
  "requirements-test.txt",
  "requirements.txt",
  "tsconfig.json",
]);
const EXCLUDED_CODE_COUNT_LANGUAGE_NAMES = new Set([
  "batch",
  "css",
  "ignore",
  "json",
  "json with comments",
  "less",
  "pip requirements",
  "properties",
  "restructuredtext",
  "sass",
  "scss",
]);
const EXCLUDED_CODE_COUNT_FORMATS = Object.freeze([
  "Batch (.bat, .cmd)",
  "CSS/styles (.css, .scss, .sass, .less)",
  "HTML (.html, .htm)",
  "Ignore files (.gitignore, .ignore, .dockerignore, .eslintignore, .npmignore)",
  "INI/config/properties (.ini, .cfg, .conf, .config, .properties, .toml)",
  "JSON (.json, .jsonc, .json5, tsconfig.json, jsconfig.json)",
  "Markdown (.md, .markdown)",
  "pip requirements (requirements*.txt, Pipfile)",
  "reStructuredText (.rst)",
  "XML (.xml, .xib, .storyboard, .plist, .svg)",
  "YAML (.yaml, .yml)",
]);
export const DEFAULT_CODE_COUNT_EXCLUDE_PATHS = Object.freeze([
  "build",
  "FreeCM",
  "thirdparty",
  "Downloads",
]);
const INTERNAL_CODE_COUNT_EXCLUDE_PATHS = Object.freeze([
  ".git",
  ".freecm/counts",
]);

// The line-classification algorithm is adapted from the MIT-licensed
// vscode-counter project, then trimmed for FreeCM's single report workflow.
export class LineCounter {
  private readonly regex: RegExp;

  constructor(
    public readonly name: string,
    private readonly lineComments: readonly string[],
    private readonly blockComments: readonly (readonly [string, string])[],
    private readonly blockStrings: readonly (readonly [string, string])[],
    lineStrings: readonly (readonly [string, string])[] = [],
    private readonly blockStringAsComment = false,
  ) {
    const filteredLineStrings = lineStrings.filter(
      (pair) =>
        blockStrings.every((block) => !pair[0].startsWith(block[0])) &&
        blockComments.every((block) => !pair[0].startsWith(block[0])),
    );
    const source = [
      blockStrings.map((value) => escapeForRegexp(value[0])).join("|"),
      blockComments.map((value) => escapeForRegexp(value[0])).join("|"),
      filteredLineStrings
        .map((value) => stringLiteralRegexpSource(value))
        .join("|"),
    ]
      .map((part) => (part === "" ? "(?!x)x" : part))
      .join(")|(");
    this.regex = new RegExp(`(${source})`, "g");
  }

  count(text: string, includeIncompleteLine = true): Count {
    const result = [0, 0, 0];
    let blockCommentEnd = "";
    let blockStringEnd = "";
    const lines = text.split(/\r\n|\r|\n/).map((line) => line.trim());
    if (!includeIncompleteLine) {
      lines.pop();
    }

    let type: LineTypeValue = LineType.Blank;
    for (const line of lines) {
      let index = 0;
      if (blockCommentEnd.length > 0) {
        type = LineType.Comment;
      } else if (blockStringEnd.length <= 0) {
        type = LineType.Blank;
      }

      while (index < line.length) {
        if (blockCommentEnd.length > 0) {
          const next = nextIndexAfter(line, blockCommentEnd, index);
          if (next >= 0) {
            blockCommentEnd = "";
            index = next;
          } else {
            break;
          }
        } else if (blockStringEnd.length > 0) {
          const next = nextIndexAfter(line, blockStringEnd, index);
          if (next >= 0) {
            blockStringEnd = "";
            index = next;
          } else {
            break;
          }
        } else if (
          this.lineComments.some((lineComment) => line.startsWith(lineComment))
        ) {
          type = LineType.Comment;
          break;
        } else {
          this.regex.lastIndex = index;
          const match = this.regex.exec(line);
          if (match === null) {
            type = LineType.Code;
            break;
          }
          if (match[1] !== undefined) {
            type =
              this.blockStringAsComment && match.index === 0
                ? LineType.Comment
                : LineType.Code;
            blockStringEnd =
              this.blockStrings.find((value) => value[0] === match[1])?.[1] ??
              "";
            index = match.index + match[1].length;
            continue;
          }
          if (match[2] !== undefined) {
            type = match.index === 0 ? LineType.Comment : LineType.Code;
            blockCommentEnd =
              this.blockComments.find((value) => value[0] === match[2])?.[1] ??
              "";
            index = match.index + match[2].length;
            continue;
          }
          type = LineType.Code;
          index += match[3]?.length ?? 1;
          break;
        }
      }
      result[type] += 1;
    }
    return new Count(
      result[LineType.Code],
      result[LineType.Comment],
      result[LineType.Blank],
    );
  }
}

export class LineCounterTable {
  private readonly idRules = new Map<string, LineCounter>();
  private readonly aliasRules = new Map<string, LineCounter>();
  private readonly extensionRules = new Map<string, LineCounter>();
  private readonly filenameRules = new Map<string, LineCounter>();

  constructor(definitions: ReadonlyMap<string, MutableLanguageDefinition>) {
    for (const [id, definition] of definitions) {
      uniqueLanguageDefinition(definition);
      const languageName = definition.aliases[0] ?? id;
      const counter = new LineCounter(
        languageName,
        definition.lineComments,
        definition.blockComments,
        definition.blockStrings,
        definition.lineStrings,
        definition.blockStringAsComment,
      );
      this.idRules.set(id.toLowerCase(), counter);
      for (const alias of definition.aliases) {
        this.aliasRules.set(alias.toLowerCase(), counter);
      }
      for (const extension of definition.extensions) {
        this.extensionRules.set(normalizeExtension(extension), counter);
      }
      for (const filename of definition.filenames) {
        this.filenameRules.set(filename.toLowerCase(), counter);
      }
    }
  }

  getCounter(filePath: string, languageId?: string): LineCounter | undefined {
    const normalizedPath = filePath.toLowerCase();
    return (
      this.filenameRules.get(path.basename(normalizedPath)) ??
      this.getById(languageId) ??
      this.extensionRules.get(
        longestKnownExtension(normalizedPath, this.extensionRules),
      ) ??
      this.extensionRules.get(path.extname(normalizedPath))
    );
  }

  private getById(languageId?: string): LineCounter | undefined {
    if (languageId === undefined) {
      return undefined;
    }
    const normalized = languageId.toLowerCase();
    return this.idRules.get(normalized) ?? this.aliasRules.get(normalized);
  }
}

export async function countCode(
  options: CountCodeOptions,
): Promise<CodeCountReport> {
  const workspaceRoot = path.resolve(options.workspaceRoot);
  const targetPath = path.resolve(options.targetPath);
  const outputRoot = path.resolve(options.outputRoot);
  const excludePaths = normalizeCodeCountExcludePaths(
    options.excludePaths ?? DEFAULT_CODE_COUNT_EXCLUDE_PATHS,
  );
  ensurePathInside(workspaceRoot, targetPath, "Code count target");
  ensurePathInside(workspaceRoot, outputRoot, "Code count output");

  const targetStats = await fs.stat(targetPath);
  if (!targetStats.isDirectory()) {
    throw new Error(`Code count target is not a directory: ${targetPath}`);
  }

  options.progress?.("Loading language definitions");
  const table = await createLineCounterTable(
    options.extensions ?? vscode.extensions.all,
    options.filesAssociations,
  );

  options.progress?.("Finding files");
  const candidateUris = await vscode.workspace.findFiles(
    new vscode.RelativePattern(vscode.Uri.file(targetPath), "**/*"),
    "{**/.git/**,**/.freecm/counts/**}",
    options.maxFiles ?? MAX_DEFAULT_FILES,
  );
  const gitignoreDirectoryRules = await loadGitignoreDirectoryRules(
    workspaceRoot,
    targetPath,
  );
  const files = candidateUris.filter(
    (uri) =>
      uri.scheme === "file" &&
      !isPathInside(outputRoot, uri.fsPath) &&
      !isInternalCodeCountPath(workspaceRoot, uri.fsPath) &&
      !isGitignoredCodeCountPath(uri.fsPath, gitignoreDirectoryRules) &&
      !isExcludedCodeCountPath(workspaceRoot, uri.fsPath, excludePaths),
  );

  options.progress?.(`Counting ${files.length} files`);
  const results = await countFiles(table, files, {
    encoding: options.encoding ?? DEFAULT_ENCODING,
    includeIncompleteLine: options.includeIncompleteLine ?? true,
    maxConcurrentReads:
      options.maxConcurrentReads ?? MAX_DEFAULT_CONCURRENT_READS,
    progress: options.progress,
  });
  if (results.length === 0) {
    throw new Error("No supported source files were found for code counting.");
  }

  const generatedAt = new Date();
  const reportDirectory = vscode.Uri.file(
    path.join(outputRoot, timestampForPath(generatedAt)),
  );
  const report = buildCodeCountReport({
    generatedAt,
    targetUri: vscode.Uri.file(targetPath),
    reportUri: vscode.Uri.joinPath(reportDirectory, "results.md"),
    files: results,
    excludePaths,
  });
  await vscode.workspace.fs.createDirectory(reportDirectory);
  await vscode.workspace.fs.writeFile(
    report.reportUri,
    Buffer.from(report.markdown, "utf8"),
  );
  return report;
}

export async function createLineCounterTable(
  extensions: readonly vscode.Extension<unknown>[] = vscode.extensions.all,
  filesAssociations?: Readonly<Record<string, string>>,
): Promise<LineCounterTable> {
  const definitions = new Map<string, MutableLanguageDefinition>();
  appendLanguageDefinitions(definitions, builtinLanguageDefinitions);
  appendFileAssociations(definitions, filesAssociations);
  await appendExtensionLanguageDefinitions(definitions, extensions);
  return new LineCounterTable(definitions);
}

export function buildCodeCountReport(input: {
  readonly generatedAt: Date;
  readonly targetUri: vscode.Uri;
  readonly reportUri: vscode.Uri;
  readonly files: readonly CodeCountFileResult[];
  readonly excludePaths?: readonly string[];
}): CodeCountReport {
  const languages = new Map<string, MutableStatistics>();
  const directories = new Map<string, MutableStatistics>();
  const total = new MutableStatistics("Total");

  for (const file of input.files) {
    total.addFile(file);
    getOrCreate(
      languages,
      file.language,
      () => new MutableStatistics(file.language),
    ).addFile(file);

    const relativeFilePath = relativePathUnderTarget(
      input.targetUri.fsPath,
      file.filename,
    );
    let directory = path.dirname(relativeFilePath);
    if (directory === ".") {
      directory = ".";
    }
    getOrCreate(
      directories,
      directory,
      () => new MutableStatistics(directory),
    ).addFile(file);
  }

  const report = {
    generatedAt: input.generatedAt,
    targetUri: input.targetUri,
    reportUri: input.reportUri,
    files: [...input.files].sort((left, right) =>
      stringCompare(left.filename, right.filename),
    ),
    total: total.snapshot(),
    languages: [...languages.values()]
      .map((entry) => entry.snapshot())
      .sort(
        (left, right) =>
          right.code - left.code || stringCompare(left.name, right.name),
      ),
    directories: [...directories.values()]
      .map((entry) => entry.snapshot())
      .sort((left, right) => stringCompare(left.name, right.name)),
    excludedPaths: normalizeCodeCountExcludePaths(input.excludePaths ?? []),
  };
  return {
    ...report,
    markdown: codeCountMarkdown(report),
  };
}

export function normalizeCodeCountTarget(
  workspaceRoot: string,
  storedPath: string | undefined,
): string {
  if (storedPath === undefined || storedPath.trim() === "") {
    return normalizePathText(workspaceRoot);
  }
  const workspace = normalizePathText(workspaceRoot);
  const candidate = normalizePathText(storedPath);
  return isPathInside(workspace, candidate) ? candidate : workspace;
}

export function codeCountExcludePathError(value: string): string | undefined {
  const normalized = normalizeCodeCountExcludePathText(value);
  if (normalized.length === 0) {
    return "Exclude path cannot be empty.";
  }
  if (normalized.startsWith("#")) {
    return "Comments are not supported in exclude paths.";
  }
  if (/[*!?]/.test(normalized)) {
    return "Wildcards and negation are not supported in exclude paths.";
  }
  if (path.isAbsolute(normalized) || /^[A-Za-z]:\//.test(normalized)) {
    return "Enter a workspace-relative path, not an absolute path.";
  }
  const trimmed = trimTrailingSlashes(normalized);
  if (trimmed.length === 0) {
    return "Exclude path cannot be empty.";
  }
  const parts = trimmed.split("/");
  if (parts.some((part) => part.length === 0)) {
    return "Exclude path cannot contain empty segments.";
  }
  if (parts.some((part) => part === "." || part === "..")) {
    return "Exclude path cannot contain . or .. segments.";
  }
  return undefined;
}

export function normalizeCodeCountExcludePaths(
  values: readonly string[],
): string[] {
  const paths: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    if (codeCountExcludePathError(value) !== undefined) {
      continue;
    }
    const normalized = normalizeCodeCountExcludePath(value);
    const key = normalized.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      paths.push(normalized);
    }
  }
  return paths;
}

export function parseCodeCountExcludePathsText(value: string): {
  readonly paths: string[];
  readonly error: string | undefined;
} {
  const paths: string[] = [];
  const seen = new Set<string>();
  const lines = value.split(/\r\n|\r|\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.trim().length === 0) {
      continue;
    }
    const error = codeCountExcludePathError(line);
    if (error !== undefined) {
      return {
        paths: [],
        error: `Line ${index + 1}: ${error}`,
      };
    }
    const normalized = normalizeCodeCountExcludePath(line);
    const key = normalized.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      paths.push(normalized);
    }
  }
  return { paths, error: undefined };
}

export function isPathInside(parentPath: string, childPath: string): boolean {
  const relative = relativePathForComparison(parentPath, childPath);
  return (
    relative === "" ||
    (!relative.startsWith("..") && !path.isAbsolute(relative))
  );
}

function ensurePathInside(
  parentPath: string,
  childPath: string,
  label: string,
): void {
  if (!isPathInside(parentPath, childPath)) {
    throw new Error(`${label} must be inside ${parentPath}: ${childPath}`);
  }
}

async function countFiles(
  table: LineCounterTable,
  files: readonly vscode.Uri[],
  options: {
    readonly encoding: BufferEncoding;
    readonly includeIncompleteLine: boolean;
    readonly maxConcurrentReads: number;
    readonly progress?: (message: string) => void;
  },
): Promise<CodeCountFileResult[]> {
  const results: CodeCountFileResult[] = [];
  let nextIndex = 0;
  let completed = 0;
  const workerCount = Math.max(
    1,
    Math.min(options.maxConcurrentReads, files.length),
  );

  async function worker(): Promise<void> {
    while (nextIndex < files.length) {
      const fileIndex = nextIndex;
      nextIndex += 1;
      const uri = files[fileIndex];
      const counter = isExcludedCodeCountFile(uri.fsPath)
        ? undefined
        : table.getCounter(uri.fsPath);
      if (counter !== undefined && !isExcludedCodeCountLanguage(counter.name)) {
        try {
          const data = await vscode.workspace.fs.readFile(uri);
          if (!looksBinary(data)) {
            const count = counter.count(
              Buffer.from(data).toString(options.encoding),
              options.includeIncompleteLine,
            );
            results.push({
              uri,
              filename: uri.fsPath,
              language: counter.name,
              code: count.code,
              comment: count.comment,
              blank: count.blank,
            });
          }
        } catch {
          // Skip unreadable files so one generated or transient file does not block the report.
        }
      }
      completed += 1;
      if (completed % 100 === 0 || completed === files.length) {
        options.progress?.(`Counting ${completed}/${files.length}`);
      }
    }
  }

  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return results;
}

async function appendExtensionLanguageDefinitions(
  definitions: Map<string, MutableLanguageDefinition>,
  extensions: readonly vscode.Extension<unknown>[],
): Promise<void> {
  const tasks: Array<Promise<void>> = [];
  for (const extension of extensions) {
    const languages = extension.packageJSON?.contributes?.languages;
    if (!Array.isArray(languages)) {
      continue;
    }
    for (const language of languages as readonly VscodeLanguageContribution[]) {
      const id = typeof language.id === "string" ? language.id : undefined;
      if (id === undefined) {
        continue;
      }
      const definition = appendLanguageDefinition(definitions, id, {
        aliases: stringArray(language.aliases),
        filenames: stringArray(language.filenames),
        extensions: stringArray(language.extensions),
      });
      if (typeof language.configuration === "string") {
        tasks.push(
          appendLanguageConfiguration(
            definition,
            extension.extensionPath,
            language.configuration,
          ),
        );
      }
    }
  }
  await Promise.all(tasks);
}

async function appendLanguageConfiguration(
  definition: MutableLanguageDefinition,
  extensionPath: string,
  configurationPath: string,
): Promise<void> {
  try {
    const raw = await fs.readFile(
      path.join(extensionPath, configurationPath),
      "utf8",
    );
    const config = JSON.parse(raw) as VscodeLanguageConfiguration;
    const comments = config.comments;
    if (typeof comments?.lineComment === "string") {
      definition.lineComments.push(comments.lineComment);
    }
    if (isStringPair(comments?.blockComment)) {
      definition.blockComments.push([
        comments.blockComment[0],
        comments.blockComment[1],
      ]);
    }
    for (const pair of languagePairs(config.autoClosingPairs)) {
      if (pair[0] !== "{" && pair[0] !== "[" && pair[0] !== "(") {
        definition.lineStrings.push(pair);
      }
    }
  } catch {
    // Extension language contributions are best-effort; malformed or missing
    // language configuration files should not block FreeCM activation.
  }
}

function appendLanguageDefinitions(
  definitions: Map<string, MutableLanguageDefinition>,
  values: Readonly<Record<string, LanguageDefinition>>,
): void {
  for (const [id, definition] of Object.entries(values)) {
    appendLanguageDefinition(definitions, id, definition);
  }
}

function appendFileAssociations(
  definitions: Map<string, MutableLanguageDefinition>,
  filesAssociations: Readonly<Record<string, string>> | undefined,
): void {
  if (filesAssociations === undefined) {
    return;
  }
  for (const [pattern, languageId] of Object.entries(filesAssociations)) {
    if (pattern.includes("*")) {
      continue;
    }
    appendLanguageDefinition(definitions, languageId, {
      filenames: [path.basename(pattern)],
      extensions: [path.extname(pattern)].filter((value) => value !== ""),
    });
  }
}

function appendLanguageDefinition(
  definitions: Map<string, MutableLanguageDefinition>,
  id: string,
  value: LanguageDefinition,
): MutableLanguageDefinition {
  const definition = getOrCreate(definitions, id.toLowerCase(), () => ({
    aliases: [],
    filenames: [],
    extensions: [],
    lineComments: [],
    blockComments: [],
    blockStrings: [],
    lineStrings: [],
  }));
  definition.aliases.push(...(value.aliases ?? []));
  definition.filenames.push(...(value.filenames ?? []));
  definition.extensions.push(...(value.extensions ?? []));
  definition.lineComments.push(...(value.lineComments ?? []));
  definition.blockComments.push(
    ...(value.blockComments ?? []).map(
      (pair) => [pair[0], pair[1]] as [string, string],
    ),
  );
  definition.blockStrings.push(
    ...(value.blockStrings ?? []).map(
      (pair) => [pair[0], pair[1]] as [string, string],
    ),
  );
  definition.lineStrings.push(
    ...(value.lineStrings ?? []).map(
      (pair) => [pair[0], pair[1]] as [string, string],
    ),
  );
  definition.blockStringAsComment =
    definition.blockStringAsComment || value.blockStringAsComment;
  return definition;
}

function uniqueLanguageDefinition(definition: MutableLanguageDefinition): void {
  definition.aliases = unique(definition.aliases);
  definition.filenames = unique(definition.filenames);
  definition.extensions = unique(definition.extensions.map(normalizeExtension));
  definition.lineComments = unique(definition.lineComments);
  definition.blockComments = uniquePairs(definition.blockComments);
  definition.blockStrings = uniquePairs(definition.blockStrings);
  definition.lineStrings = uniquePairs(definition.lineStrings).filter(
    (pair) =>
      definition.blockStrings.every((block) => !pair[0].startsWith(block[0])) &&
      definition.blockComments.every((block) => !pair[0].startsWith(block[0])),
  );
}

function codeCountMarkdown(report: Omit<CodeCountReport, "markdown">): string {
  const lines = [
    "# FreeCM Code Count",
    "",
    `Date: ${formatDate(report.generatedAt)}`,
    `Directory: ${report.targetUri.fsPath}`,
    `Total: ${formatNumber(report.total.files)} files, ${formatNumber(report.total.code)} code, ${formatNumber(report.total.comment)} comments, ${formatNumber(report.total.blank)} blanks, ${formatNumber(report.total.total)} lines`,
    "",
    "## Languages",
    "",
    statisticsTable(report.languages, "language"),
    "",
    "## Directories",
    "",
    statisticsTable(report.directories, "path"),
    "",
    "## Files",
    "",
    "| file | language | code | comment | blank | total |",
    "| :--- | :--- | ---: | ---: | ---: | ---: |",
    ...report.files
      .map((file) => {
        const relativePath =
          relativePathUnderTarget(report.targetUri.fsPath, file.filename) ||
          path.basename(file.filename);
        return [
          markdownCell(relativePath),
          markdownCell(file.language),
          formatNumber(file.code),
          formatNumber(file.comment),
          formatNumber(file.blank),
          formatNumber(file.code + file.comment + file.blank),
        ].join(" | ");
      })
      .map((line) => `| ${line} |`),
    "",
    "## Excluded Formats And Paths",
    "",
    ...EXCLUDED_CODE_COUNT_FORMATS.map((format) => `- ${format}`),
    ...report.excludedPaths.map((excludedPath) => `- ${excludedPath}`),
    "",
  ];
  return lines.join("\n");
}

function statisticsTable(
  statistics: readonly CodeCountStatistics[],
  nameColumn: string,
): string {
  return [
    `| ${nameColumn} | files | code | comment | blank | total |`,
    "| :--- | ---: | ---: | ---: | ---: | ---: |",
    ...statistics
      .map((entry) =>
        [
          markdownCell(entry.name),
          formatNumber(entry.files),
          formatNumber(entry.code),
          formatNumber(entry.comment),
          formatNumber(entry.blank),
          formatNumber(entry.total),
        ].join(" | "),
      )
      .map((line) => `| ${line} |`),
  ].join("\n");
}

function looksBinary(data: Uint8Array): boolean {
  const limit = Math.min(data.length, 4096);
  for (let index = 0; index < limit; index += 1) {
    if (data[index] === 0) {
      return true;
    }
  }
  return false;
}

function isExcludedCodeCountFile(filePath: string): boolean {
  const basename = path.basename(filePath).toLowerCase();
  return (
    EXCLUDED_CODE_COUNT_FILENAMES.has(basename) ||
    EXCLUDED_CODE_COUNT_EXTENSIONS.has(path.extname(basename))
  );
}

function isExcludedCodeCountPath(
  workspaceRoot: string,
  filePath: string,
  excludePaths: readonly string[],
): boolean {
  const relativePath = path.relative(workspaceRoot, filePath);
  if (
    relativePath === "" ||
    relativePath.startsWith("..") ||
    path.isAbsolute(relativePath)
  ) {
    return false;
  }
  const normalizedRelativePath =
    normalizeCodeCountExcludePath(relativePath).toLowerCase();
  const relativeParts = normalizedRelativePath.split("/");
  return excludePaths.some((excludePath) => {
    const normalizedExcludePath =
      normalizeCodeCountExcludePath(excludePath).toLowerCase();
    if (!normalizedExcludePath.includes("/")) {
      return relativeParts.some((part) => part === normalizedExcludePath);
    }
    return (
      normalizedRelativePath === normalizedExcludePath ||
      normalizedRelativePath.startsWith(`${normalizedExcludePath}/`)
    );
  });
}

async function loadGitignoreDirectoryRules(
  workspaceRoot: string,
  targetPath: string,
): Promise<GitignoreDirectoryRule[]> {
  const gitignoreUris = await vscode.workspace.findFiles(
    new vscode.RelativePattern(vscode.Uri.file(workspaceRoot), "**/.gitignore"),
    "{**/.git/**,**/.freecm/counts/**}",
    MAX_DEFAULT_FILES,
  );
  const rules: GitignoreDirectoryRule[] = [];
  for (const uri of gitignoreUris) {
    if (uri.scheme !== "file") {
      continue;
    }
    const gitignorePath = uri.fsPath;
    if (path.basename(gitignorePath) !== ".gitignore") {
      continue;
    }
    const basePath = path.dirname(gitignorePath);
    if (
      !isPathInside(workspaceRoot, gitignorePath) ||
      !gitignoreScopeCanAffectTarget(basePath, targetPath)
    ) {
      continue;
    }
    try {
      const content = await fs.readFile(gitignorePath, "utf8");
      rules.push(...parseGitignoreDirectoryRules(basePath, content));
    } catch {
      // Missing or unreadable ignore files should not block code counting.
    }
  }
  return rules;
}

function parseGitignoreDirectoryRules(
  basePath: string,
  content: string,
): GitignoreDirectoryRule[] {
  const rules: GitignoreDirectoryRule[] = [];
  for (const rawLine of content.split(/\r\n|\r|\n/)) {
    const parsed = parseGitignoreDirectoryPattern(rawLine);
    if (parsed !== undefined) {
      rules.push({
        basePath,
        path: parsed.path,
        matchAnywhere: parsed.matchAnywhere,
      });
    }
  }
  return rules;
}

function parseGitignoreDirectoryPattern(
  rawLine: string,
): { readonly path: string; readonly matchAnywhere: boolean } | undefined {
  let line = rawLine.trim();
  if (line.length === 0 || line.startsWith("#") || line.startsWith("!")) {
    return undefined;
  }
  if (line.startsWith("\\#") || line.startsWith("\\!")) {
    line = line.slice(1);
  }
  if (hasGitignoreGlobCharacters(line)) {
    return undefined;
  }
  const anchored = line.startsWith("/");
  if (anchored) {
    line = line.slice(1);
  }
  const normalized = normalizeCodeCountExcludePath(line);
  if (normalized.length === 0) {
    return undefined;
  }
  const parts = normalized.split("/");
  if (parts.some((part) => part === "." || part === ".." || part === "")) {
    return undefined;
  }
  return {
    path: normalized,
    matchAnywhere: !anchored && parts.length === 1,
  };
}

function hasGitignoreGlobCharacters(value: string): boolean {
  return /[*?[\]]/.test(value);
}

function gitignoreScopeCanAffectTarget(
  gitignoreBasePath: string,
  targetPath: string,
): boolean {
  return (
    isPathInside(gitignoreBasePath, targetPath) ||
    isPathInside(targetPath, gitignoreBasePath)
  );
}

function isGitignoredCodeCountPath(
  filePath: string,
  rules: readonly GitignoreDirectoryRule[],
): boolean {
  for (const rule of rules) {
    if (!isPathInside(rule.basePath, filePath)) {
      continue;
    }
    const relativePath = normalizeCodeCountExcludePath(
      path.relative(rule.basePath, filePath),
    ).toLowerCase();
    const rulePath = normalizeCodeCountExcludePath(rule.path).toLowerCase();
    if (rule.matchAnywhere) {
      const relativeParts = relativePath.split("/");
      if (relativeParts.some((part) => part === rulePath)) {
        return true;
      }
    } else if (
      relativePath === rulePath ||
      relativePath.startsWith(`${rulePath}/`)
    ) {
      return true;
    }
  }
  return false;
}

function isInternalCodeCountPath(
  workspaceRoot: string,
  filePath: string,
): boolean {
  return isExcludedCodeCountPath(
    workspaceRoot,
    filePath,
    INTERNAL_CODE_COUNT_EXCLUDE_PATHS,
  );
}

function isExcludedCodeCountLanguage(languageName: string): boolean {
  return EXCLUDED_CODE_COUNT_LANGUAGE_NAMES.has(languageName.toLowerCase());
}

function languagePairs(value: unknown): Array<[string, string]> {
  if (!Array.isArray(value)) {
    return [];
  }
  const pairs: Array<[string, string]> = [];
  for (const item of value) {
    if (isStringPair(item)) {
      pairs.push([item[0], item[1]]);
    } else if (
      typeof item === "object" &&
      item !== null &&
      typeof (item as { open?: unknown }).open === "string" &&
      typeof (item as { close?: unknown }).close === "string"
    ) {
      pairs.push([
        (item as { open: string }).open,
        (item as { close: string }).close,
      ]);
    }
  }
  return pairs;
}

function isStringPair(value: unknown): value is readonly [string, string] {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    typeof value[0] === "string" &&
    typeof value[1] === "string"
  );
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function unique(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value !== ""))];
}

function uniquePairs(
  values: readonly [string, string][],
): Array<[string, string]> {
  return [
    ...new Map(
      values
        .filter((pair) => pair[0] !== "")
        .map((pair) => [pair[0], pair] as const),
    ).values(),
  ];
}

function normalizeExtension(extension: string): string {
  return extension.startsWith(".")
    ? extension.toLowerCase()
    : `.${extension.toLowerCase()}`;
}

function longestKnownExtension(
  filePath: string,
  rules: ReadonlyMap<string, LineCounter>,
): string {
  let selected = "";
  for (const extension of rules.keys()) {
    if (filePath.endsWith(extension) && extension.length > selected.length) {
      selected = extension;
    }
  }
  return selected;
}

function nextIndexAfter(
  value: string,
  searchValue: string,
  fromIndex: number,
): number {
  const index = value.indexOf(searchValue, fromIndex);
  return index >= 0 ? index + searchValue.length : index;
}

const regexEscapePattern = /[.*+?^${}()|[\]\\]/g;

function escapeForRegexp(value: string): string {
  return value.replace(regexEscapePattern, "\\$&");
}

function stringLiteralRegexpSource([start, end]: readonly [
  string,
  string,
]): string {
  const escapedStart = escapeForRegexp(start);
  const escapedEnd = escapeForRegexp(end);
  return `${escapedStart}(?:\\\\.|[^${escapedEnd}\\\\])*${escapedEnd}`;
}

function getOrCreate<K, V>(map: Map<K, V>, key: K, create: () => V): V {
  const existing = map.get(key);
  if (existing !== undefined) {
    return existing;
  }
  const value = create();
  map.set(key, value);
  return value;
}

function timestampForPath(date: Date): string {
  const pad = (value: number) => value.toString().padStart(2, "0");
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
}

function formatDate(date: Date): string {
  const pad = (value: number) => value.toString().padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

function markdownCell(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/\|/g, "\\|").replace(/\n/g, " ");
}

function stringCompare(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function normalizeRelativePath(value: string): string {
  return value.replace(/\\/g, "/");
}

function normalizeCodeCountExcludePathText(value: string): string {
  return normalizeRelativePath(value.trim());
}

function normalizeCodeCountExcludePath(value: string): string {
  return trimTrailingSlashes(normalizeCodeCountExcludePathText(value));
}

function trimTrailingSlashes(value: string): string {
  let trimmed = value;
  while (trimmed.endsWith("/")) {
    trimmed = trimmed.slice(0, -1);
  }
  return trimmed;
}

function normalizePathText(value: string): string {
  return normalizeRelativePath(path.normalize(value));
}

function relativePathForComparison(
  parentPath: string,
  childPath: string,
): string {
  if (
    looksLikePosixAbsolutePath(parentPath) ||
    looksLikePosixAbsolutePath(childPath)
  ) {
    const relative = path.posix.relative(
      normalizeRelativePath(parentPath),
      normalizeRelativePath(childPath),
    );
    return normalizeRelativePath(relative);
  }
  return normalizeRelativePath(
    path.relative(path.normalize(parentPath), path.normalize(childPath)),
  );
}

function relativePathUnderTarget(targetPath: string, filePath: string): string {
  if (
    looksLikePosixAbsolutePath(targetPath) ||
    looksLikePosixAbsolutePath(filePath)
  ) {
    return normalizeRelativePath(
      path.posix.relative(
        normalizeRelativePath(targetPath),
        normalizeRelativePath(filePath),
      ),
    );
  }
  return normalizeRelativePath(
    path.relative(path.normalize(targetPath), path.normalize(filePath)),
  );
}

function looksLikePosixAbsolutePath(value: string): boolean {
  return value.startsWith("/") && !value.startsWith("//");
}

class MutableStatistics extends Count {
  files = 0;

  constructor(readonly name: string) {
    super();
  }

  addFile(value: LineCount): MutableStatistics {
    this.files += 1;
    this.add(value);
    return this;
  }

  snapshot(): CodeCountStatistics {
    return {
      name: this.name,
      files: this.files,
      code: this.code,
      comment: this.comment,
      blank: this.blank,
      total: this.total,
    };
  }
}

const LineType = {
  Code: 0,
  Comment: 1,
  Blank: 2,
} as const;

type LineTypeValue = (typeof LineType)[keyof typeof LineType];

const builtinLanguageDefinitions: Readonly<Record<string, LanguageDefinition>> =
  {
    c: {
      aliases: ["C", "c"],
      extensions: [".c", ".h"],
      lineComments: ["//"],
      blockComments: [["/*", "*/"]],
      lineStrings: [
        ["'", "'"],
        ['"', '"'],
      ],
    },
    cpp: {
      aliases: ["C++", "Cpp", "cpp"],
      extensions: [
        ".cpp",
        ".cppm",
        ".cc",
        ".cxx",
        ".c++",
        ".hpp",
        ".hh",
        ".hxx",
        ".h++",
        ".ii",
        ".ino",
        ".inl",
        ".ipp",
        ".ixx",
        ".tpp",
        ".txx",
        ".hpp.in",
        ".h.in",
      ],
      lineComments: ["//"],
      blockComments: [["/*", "*/"]],
      blockStrings: [
        ['R"(', ')"'],
        ['R"tag(', ')tag"'],
      ],
      lineStrings: [
        ["'", "'"],
        ['"', '"'],
      ],
    },
    objectivec: {
      aliases: ["Objective-C", "objective-c"],
      extensions: [".m", ".mm"],
      lineComments: ["//"],
      blockComments: [["/*", "*/"]],
      lineStrings: [
        ["@", '"'],
        ['"', '"'],
        ["'", "'"],
      ],
    },
    swift: {
      aliases: ["Swift", "swift"],
      extensions: [".swift"],
      lineComments: ["//"],
      blockComments: [["/*", "*/"]],
      blockStrings: [['"""', '"""']],
      lineStrings: [
        ['"', '"'],
        ["'", "'"],
      ],
    },
    kotlin: {
      aliases: ["Kotlin", "kotlin", "kt"],
      extensions: [".kt", ".kts"],
      lineComments: ["//"],
      blockComments: [["/*", "*/"]],
      blockStrings: [['"""', '"""']],
      lineStrings: [
        ['"', '"'],
        ["'", "'"],
      ],
    },
    python: {
      aliases: ["Python", "python", "py"],
      extensions: [".py", ".pyw", ".pyi"],
      lineComments: ["#"],
      blockComments: [
        ['"""', '"""'],
        ["'''", "'''"],
      ],
      blockStrings: [
        ['"""', '"""'],
        ["'''", "'''"],
      ],
      lineStrings: [
        ['"', '"'],
        ["'", "'"],
      ],
      blockStringAsComment: true,
    },
    javascript: {
      aliases: ["JavaScript", "javascript", "js"],
      filenames: ["jakefile"],
      extensions: [".js", ".mjs", ".cjs", ".jsx"],
      lineComments: ["//"],
      blockComments: [["/*", "*/"]],
      blockStrings: [["`", "`"]],
      lineStrings: [
        ["'", "'"],
        ['"', '"'],
      ],
    },
    typescript: {
      aliases: ["TypeScript", "typescript", "ts"],
      extensions: [".ts", ".mts", ".cts", ".tsx"],
      lineComments: ["//"],
      blockComments: [["/*", "*/"]],
      blockStrings: [["`", "`"]],
      lineStrings: [
        ["'", "'"],
        ['"', '"'],
      ],
    },
    shellscript: {
      aliases: ["Shell", "Shell Script", "sh", "shellscript"],
      filenames: [".bashrc", ".zshrc", "bashrc", "zshrc"],
      extensions: [".sh", ".bash", ".zsh", ".fish"],
      lineComments: ["#"],
      lineStrings: [
        ['"', '"'],
        ["'", "'"],
      ],
    },
    cmake: {
      aliases: ["CMake", "cmake"],
      filenames: ["cmakelists.txt"],
      extensions: [".cmake"],
      lineComments: ["#"],
      blockComments: [["#[[", "]]"]],
      lineStrings: [['"', '"']],
    },
    yaml: {
      aliases: ["YAML", "yaml", "yml"],
      extensions: [".yaml", ".yml"],
      lineComments: ["#"],
      lineStrings: [
        ['"', '"'],
        ["'", "'"],
      ],
    },
    markdown: {
      aliases: ["Markdown", "markdown"],
      extensions: [".md", ".markdown"],
      blockComments: [["<!--", "-->"]],
    },
    xml: {
      aliases: ["XML", "xml"],
      extensions: [".xml", ".xib", ".storyboard", ".plist", ".svg"],
      blockComments: [["<!--", "-->"]],
      lineStrings: [
        ['"', '"'],
        ["'", "'"],
      ],
    },
    html: {
      aliases: ["HTML", "html"],
      extensions: [".html", ".htm"],
      blockComments: [["<!--", "-->"]],
      lineStrings: [
        ['"', '"'],
        ["'", "'"],
      ],
    },
    css: {
      aliases: ["CSS", "css"],
      extensions: [".css"],
      blockComments: [["/*", "*/"]],
      lineStrings: [
        ['"', '"'],
        ["'", "'"],
      ],
    },
  };
