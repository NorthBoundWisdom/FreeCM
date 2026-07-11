import * as vscode from "vscode";

export interface LineCount {
  readonly code: number;
  readonly comment: number;
  readonly blank: number;
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

export interface CodeCountSkippedFile {
  readonly filename: string;
  readonly reason: "binary" | "large" | "unreadable";
}

export interface CodeCountReport {
  readonly generatedAt: Date;
  readonly targetUri: vscode.Uri;
  readonly reportUri: vscode.Uri;
  readonly files: readonly CodeCountFileResult[];
  readonly skippedFiles: readonly CodeCountSkippedFile[];
  readonly warnings: readonly string[];
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
  readonly maxFileBytes?: number;
  readonly maxConcurrentReads?: number;
  readonly reportRetention?: number;
  readonly includeIncompleteLine?: boolean;
  readonly excludePaths?: readonly string[];
  readonly cancellationToken?: vscode.CancellationToken;
  readonly progress?: (message: string) => void;
}
