import * as fs from "fs/promises";
import * as path from "path";
import * as vscode from "vscode";
import { beginFilesystemRead } from "../performanceMetrics";
import {
  LanguageDefinition,
  LineCounterTable,
  MutableLanguageDefinition,
} from "./lineCounter";

interface LanguageContribution {
  readonly id?: unknown;
  readonly aliases?: unknown;
  readonly filenames?: unknown;
  readonly extensions?: unknown;
  readonly configuration?: unknown;
}

interface LanguageConfiguration {
  readonly comments?: { readonly lineComment?: unknown; readonly blockComment?: unknown };
  readonly autoClosingPairs?: unknown;
}

let cachedTable: { readonly key: string; readonly value: Promise<LineCounterTable> } | undefined;

export function createLineCounterTable(
  extensions: readonly vscode.Extension<unknown>[] = vscode.extensions.all,
  filesAssociations?: Readonly<Record<string, string>>,
): Promise<LineCounterTable> {
  const key = languageCacheKey(extensions, filesAssociations);
  if (cachedTable?.key === key) return cachedTable.value;
  const value = buildLineCounterTable(extensions, filesAssociations).catch((error) => {
    if (cachedTable?.value === value) cachedTable = undefined;
    throw error;
  });
  cachedTable = { key, value };
  return value;
}

export function clearLanguageTableCache(): void {
  cachedTable = undefined;
}

async function buildLineCounterTable(
  extensions: readonly vscode.Extension<unknown>[],
  associations: Readonly<Record<string, string>> | undefined,
): Promise<LineCounterTable> {
  const definitions = new Map<string, MutableLanguageDefinition>();
  for (const [id, definition] of Object.entries(BUILTIN_LANGUAGES)) {
    appendDefinition(definitions, id, definition);
  }
  appendAssociations(definitions, associations);
  const tasks: Array<Promise<void>> = [];
  for (const extension of extensions) {
    const languages = extension.packageJSON?.contributes?.languages;
    if (!Array.isArray(languages)) continue;
    for (const language of languages as readonly LanguageContribution[]) {
      if (typeof language.id !== "string") continue;
      const definition = appendDefinition(definitions, language.id, {
        aliases: stringArray(language.aliases),
        filenames: stringArray(language.filenames),
        extensions: stringArray(language.extensions),
      });
      if (typeof language.configuration === "string") {
        tasks.push(loadConfiguration(definition, extension.extensionPath, language.configuration));
      }
    }
  }
  await Promise.all(tasks);
  return new LineCounterTable(definitions);
}

async function loadConfiguration(
  definition: MutableLanguageDefinition,
  extensionRoot: string,
  relativePath: string,
): Promise<void> {
  const finish = beginFilesystemRead();
  try {
    const raw = await fs.readFile(path.join(extensionRoot, relativePath), "utf8");
    const config = JSON.parse(raw) as LanguageConfiguration;
    if (typeof config.comments?.lineComment === "string") {
      definition.lineComments.push(config.comments.lineComment);
    }
    if (isStringPair(config.comments?.blockComment)) {
      definition.blockComments.push([...config.comments.blockComment]);
    }
    for (const pair of languagePairs(config.autoClosingPairs)) {
      if (!"{[(".includes(pair[0])) definition.lineStrings.push(pair);
    }
  } catch {
    // Installed extension language metadata is best-effort.
  } finally {
    finish();
  }
}

function languageCacheKey(
  extensions: readonly vscode.Extension<unknown>[],
  associations: Readonly<Record<string, string>> | undefined,
): string {
  const extensionKey = extensions.map((extension) => {
    const languages = extension.packageJSON?.contributes?.languages;
    return `${extension.id}:${extension.packageJSON?.version ?? ""}:${JSON.stringify(languages ?? [])}`;
  });
  return `${extensionKey.join("|")}\n${JSON.stringify(Object.entries(associations ?? {}).sort())}`;
}

function appendAssociations(
  definitions: Map<string, MutableLanguageDefinition>,
  associations: Readonly<Record<string, string>> | undefined,
): void {
  for (const [pattern, languageId] of Object.entries(associations ?? {})) {
    if (pattern.includes("*")) continue;
    appendDefinition(definitions, languageId, {
      filenames: [path.basename(pattern)],
      extensions: [path.extname(pattern)].filter(Boolean),
    });
  }
}

function appendDefinition(
  definitions: Map<string, MutableLanguageDefinition>,
  id: string,
  value: LanguageDefinition,
): MutableLanguageDefinition {
  const key = id.toLowerCase();
  let definition = definitions.get(key);
  if (definition === undefined) {
    definition = {
      aliases: [], filenames: [], extensions: [], lineComments: [],
      blockComments: [], blockStrings: [], lineStrings: [],
    };
    definitions.set(key, definition);
  }
  definition.aliases.push(...(value.aliases ?? []));
  definition.filenames.push(...(value.filenames ?? []));
  definition.extensions.push(...(value.extensions ?? []));
  definition.lineComments.push(...(value.lineComments ?? []));
  definition.blockComments.push(...(value.blockComments ?? []).map((pair) => [pair[0], pair[1]] as [string, string]));
  definition.blockStrings.push(...(value.blockStrings ?? []).map((pair) => [pair[0], pair[1]] as [string, string]));
  definition.lineStrings.push(...(value.lineStrings ?? []).map((pair) => [pair[0], pair[1]] as [string, string]));
  definition.blockStringAsComment ||= value.blockStringAsComment;
  return definition;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function isStringPair(value: unknown): value is readonly [string, string] {
  return Array.isArray(value) && value.length === 2 && value.every((item) => typeof item === "string");
}

function languagePairs(value: unknown): Array<[string, string]> {
  if (!Array.isArray(value)) return [];
  const pairs: Array<[string, string]> = [];
  for (const item of value) {
    if (isStringPair(item)) pairs.push([...item]);
    else if (item !== null && typeof item === "object") {
      const pair = item as { open?: unknown; close?: unknown };
      if (typeof pair.open === "string" && typeof pair.close === "string") pairs.push([pair.open, pair.close]);
    }
  }
  return pairs;
}

const cStyle = {
  lineComments: ["//"], blockComments: [["/*", "*/"]] as const,
  lineStrings: [["'", "'"], ['"', '"']] as const,
};

const BUILTIN_LANGUAGES: Readonly<Record<string, LanguageDefinition>> = {
  c: { aliases: ["C"], extensions: [".c", ".h"], ...cStyle },
  cpp: {
    aliases: ["C++", "Cpp"],
    extensions: [".cpp", ".cppm", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++", ".ii", ".ino", ".inl", ".ipp", ".ixx", ".tpp", ".txx", ".hpp.in", ".h.in"],
    blockStrings: [['R"(', ')"'], ['R"tag(', ')tag"']],
    ...cStyle,
  },
  objectivec: { aliases: ["Objective-C"], extensions: [".m", ".mm"], ...cStyle },
  swift: { aliases: ["Swift"], extensions: [".swift"], blockStrings: [['"""', '"""']], ...cStyle },
  kotlin: { aliases: ["Kotlin"], extensions: [".kt", ".kts"], blockStrings: [['"""', '"""']], ...cStyle },
  shader: { aliases: ["Shader", "GLSL"], extensions: [".glsl", ".vert", ".frag", ".geom", ".tesc", ".tese", ".comp", ".mesh", ".task", ".rgen", ".rmiss", ".rchit", ".rahit", ".rint", ".rcall"], ...cStyle },
  java: { aliases: ["Java"], extensions: [".java"], ...cStyle },
  csharp: { aliases: ["C#"], extensions: [".cs"], ...cStyle },
  rust: { aliases: ["Rust"], extensions: [".rs"], ...cStyle },
  go: { aliases: ["Go"], extensions: [".go"], ...cStyle },
  python: {
    aliases: ["Python"], extensions: [".py", ".pyw", ".pyi"], lineComments: ["#"],
    blockComments: [['"""', '"""'], ["'''", "'''"]],
    blockStrings: [['"""', '"""'], ["'''", "'''"]],
    lineStrings: [['"', '"'], ["'", "'"]], blockStringAsComment: true,
  },
  javascript: { aliases: ["JavaScript"], filenames: ["Jakefile"], extensions: [".js", ".mjs", ".cjs", ".jsx"], blockStrings: [["`", "`"]], ...cStyle },
  typescript: { aliases: ["TypeScript"], extensions: [".ts", ".mts", ".cts", ".tsx"], blockStrings: [["`", "`"]], ...cStyle },
  shellscript: { aliases: ["Shell"], filenames: [".bashrc", ".zshrc", "bashrc", "zshrc"], extensions: [".sh", ".bash", ".zsh", ".fish"], lineComments: ["#"], lineStrings: [['"', '"'], ["'", "'"]] },
  cmake: { aliases: ["CMake"], filenames: ["CMakeLists.txt"], extensions: [".cmake"], lineComments: ["#"], blockComments: [["#[[", "]]" ]], lineStrings: [['"', '"']] },
};
