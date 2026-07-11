import * as path from "path";
import { LineCount } from "./types";

export class Count implements LineCount {
  constructor(
    public code = 0,
    public comment = 0,
    public blank = 0,
  ) {}

  get total(): number {
    return this.code + this.comment + this.blank;
  }

  add(value: LineCount): Count {
    this.code += value.code;
    this.comment += value.comment;
    this.blank += value.blank;
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

const LineType = { Code: 0, Comment: 1, Blank: 2 } as const;
type LineTypeValue = (typeof LineType)[keyof typeof LineType];

// Adapted from the MIT-licensed vscode-counter classifier. It scans ranges in
// the original string, avoiding a trimmed string allocation for every line.
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
    const filtered = lineStrings.filter(
      (pair) =>
        blockStrings.every((block) => !pair[0].startsWith(block[0])) &&
        blockComments.every((block) => !pair[0].startsWith(block[0])),
    );
    const source = [
      blockStrings.map((pair) => escapeRegexp(pair[0])).join("|"),
      blockComments.map((pair) => escapeRegexp(pair[0])).join("|"),
      filtered.map(stringLiteralRegexpSource).join("|"),
    ]
      .map((part) => (part === "" ? "(?!x)x" : part))
      .join(")|(");
    this.regex = new RegExp(`(${source})`, "g");
  }

  count(text: string, includeIncompleteLine = true): Count {
    const result = [0, 0, 0];
    let blockCommentEnd = "";
    let blockStringEnd = "";
    let blockStringType: LineTypeValue = LineType.Code;
    let lineStart = 0;
    while (lineStart <= text.length) {
      const separator = nextLineSeparator(text, lineStart);
      if (separator === undefined && !includeIncompleteLine) {
        break;
      }
      const lineEnd = separator?.index ?? text.length;
      let start = lineStart;
      let end = lineEnd;
      while (start < end && isWhitespace(text.charCodeAt(start))) start += 1;
      while (end > start && isWhitespace(text.charCodeAt(end - 1))) end -= 1;

      let type: LineTypeValue =
        blockCommentEnd !== ""
          ? LineType.Comment
          : blockStringEnd === ""
            ? LineType.Blank
            : blockStringType;
      let index = start;
      while (index < end) {
        if (blockCommentEnd !== "") {
          const next = nextIndexAfter(text, blockCommentEnd, index, end);
          if (next < 0) break;
          blockCommentEnd = "";
          index = next;
        } else if (blockStringEnd !== "") {
          const next = nextIndexAfter(text, blockStringEnd, index, end);
          if (next < 0) break;
          blockStringEnd = "";
          index = next;
        } else if (
          this.lineComments.some((comment) => text.startsWith(comment, index))
        ) {
          type = LineType.Comment;
          break;
        } else {
          this.regex.lastIndex = index;
          const match = this.regex.exec(text);
          if (match === null || match.index >= end) {
            type = LineType.Code;
            break;
          }
          if (match[1] !== undefined) {
            type =
              this.blockStringAsComment && match.index === start
                ? LineType.Comment
                : LineType.Code;
            blockStringType = type;
            blockStringEnd =
              this.blockStrings.find((pair) => pair[0] === match[1])?.[1] ?? "";
            index = match.index + match[1].length;
          } else if (match[2] !== undefined) {
            type = match.index === start ? LineType.Comment : LineType.Code;
            blockCommentEnd =
              this.blockComments.find((pair) => pair[0] === match[2])?.[1] ?? "";
            index = match.index + match[2].length;
          } else {
            type = LineType.Code;
            break;
          }
        }
      }
      result[type] += 1;
      if (separator === undefined) break;
      lineStart = separator.index + separator.length;
    }
    return new Count(result[0], result[1], result[2]);
  }
}

export class LineCounterTable {
  private readonly idRules = new Map<string, LineCounter>();
  private readonly aliasRules = new Map<string, LineCounter>();
  private readonly extensionRules = new Map<string, LineCounter>();
  private readonly extensionRulesByLastSuffix = new Map<string, string[]>();
  private readonly filenameRules = new Map<string, LineCounter>();
  private readonly candidatePatterns = new Set<string>();
  readonly version: string;

  constructor(definitions: ReadonlyMap<string, MutableLanguageDefinition>) {
    const versionParts: string[] = [];
    for (const [id, raw] of [...definitions].sort(([a], [b]) => a.localeCompare(b))) {
      const definition = normalizedDefinition(raw);
      const counter = new LineCounter(
        definition.aliases[0] ?? id,
        definition.lineComments,
        definition.blockComments,
        definition.blockStrings,
        definition.lineStrings,
        definition.blockStringAsComment,
      );
      this.idRules.set(id.toLowerCase(), counter);
      for (const alias of definition.aliases) this.aliasRules.set(alias.toLowerCase(), counter);
      for (const filename of definition.filenames) this.filenameRules.set(filename.toLowerCase(), counter);
      for (const filename of raw.filenames) {
        if (!/[?*{}[\]]/.test(filename)) {
          this.candidatePatterns.add(`**/${caseInsensitiveGlobLiteral(filename)}`);
        }
      }
      for (const extension of definition.extensions) {
        this.extensionRules.set(extension, counter);
        const last = path.extname(`file${extension}`);
        const candidates = this.extensionRulesByLastSuffix.get(last) ?? [];
        candidates.push(extension);
        candidates.sort((left, right) => right.length - left.length);
        this.extensionRulesByLastSuffix.set(last, candidates);
        if (/^\.[A-Za-z0-9+_.-]+$/.test(extension)) {
          this.candidatePatterns.add(`**/*${caseInsensitiveGlobLiteral(extension)}`);
        }
      }
      versionParts.push(`${id}:${JSON.stringify(definition)}`);
    }
    this.version = versionParts.join("|");
  }

  getCounter(filePath: string, languageId?: string): LineCounter | undefined {
    const normalized = filePath.toLowerCase();
    const basename = path.basename(normalized);
    const byId = languageId === undefined
      ? undefined
      : this.idRules.get(languageId.toLowerCase()) ?? this.aliasRules.get(languageId.toLowerCase());
    if (this.filenameRules.has(basename)) return this.filenameRules.get(basename);
    if (byId !== undefined) return byId;
    const suffixes = this.extensionRulesByLastSuffix.get(path.extname(normalized)) ?? [];
    const extension = suffixes.find((candidate) => normalized.endsWith(candidate));
    return extension === undefined ? undefined : this.extensionRules.get(extension);
  }

  candidateGlob(): string {
    const entries = [...this.candidatePatterns];
    return entries.length === 1 ? entries[0] : `{${entries.join(",")}}`;
  }
}

function normalizedDefinition(value: MutableLanguageDefinition): MutableLanguageDefinition {
  return {
    aliases: unique(value.aliases),
    filenames: unique(value.filenames.map((item) => item.toLowerCase())),
    extensions: unique(value.extensions.map(normalizeExtension)),
    lineComments: unique(value.lineComments),
    blockComments: uniquePairs(value.blockComments),
    blockStrings: uniquePairs(value.blockStrings),
    lineStrings: uniquePairs(value.lineStrings),
    blockStringAsComment: value.blockStringAsComment,
  };
}

function nextLineSeparator(text: string, from: number): { index: number; length: number } | undefined {
  for (let index = from; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code === 10) return { index, length: 1 };
    if (code === 13) return { index, length: text.charCodeAt(index + 1) === 10 ? 2 : 1 };
  }
  return undefined;
}

function isWhitespace(code: number): boolean {
  return code === 32 || code === 9 || code === 11 || code === 12;
}

function nextIndexAfter(text: string, search: string, from: number, end: number): number {
  const index = text.indexOf(search, from);
  return index >= 0 && index < end ? index + search.length : -1;
}

function normalizeExtension(value: string): string {
  const normalized = value.toLowerCase();
  return normalized.startsWith(".") ? normalized : `.${normalized}`;
}

function caseInsensitiveGlobLiteral(value: string): string {
  return [...value]
    .map((character) =>
      /[A-Za-z]/.test(character)
        ? `[${character.toLowerCase()}${character.toUpperCase()}]`
        : character,
    )
    .join("");
}

function unique(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value !== ""))];
}

function uniquePairs(values: readonly (readonly [string, string])[]): Array<[string, string]> {
  return [...new Map(values.map((pair) => [`${pair[0]}\0${pair[1]}`, [pair[0], pair[1]] as [string, string]])).values()];
}

const regexEscapePattern = /[.*+?^${}()|[\]\\]/g;
function escapeRegexp(value: string): string {
  return value.replace(regexEscapePattern, "\\$&");
}

function stringLiteralRegexpSource([start, end]: readonly [string, string]): string {
  return `${escapeRegexp(start)}(?:\\\\.|[^${escapeRegexp(end)}\\\\])*${escapeRegexp(end)}`;
}
