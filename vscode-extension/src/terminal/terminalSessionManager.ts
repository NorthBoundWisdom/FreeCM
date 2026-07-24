import { randomUUID } from "crypto";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import { RepoCommandAction } from "../repoCommands";
import { TerminalLogger, TerminalLogLevel } from "../terminalLogger";
import { terminalPathEnvironmentForRepo } from "../terminalPath";
import { RepoWorkspaceFolder } from "../workspaceDiscovery";

import {
  errorMessage,
  isDisposedTerminalError,
  terminalCommandSequence,
  terminalCompletionCommand,
  TerminalProfile,
  terminalBootstrapOptions,
  terminalProfilesEqual,
  usesRuntimeTerminalPath,
  waitForTerminalExecutionEnd,
} from "./terminalRuntime";

const TERMINAL_NAME = "FreeCM";
const LOG_TERMINAL_NAME = "FreeCM Log";
const COMMAND_COMPLETION_DIRECTORY = path.join(
  os.tmpdir(),
  "freecm-terminal-completions",
);

interface TerminalExecutionResult {
  readonly exitCode: number | undefined;
  readonly terminalClosed: boolean;
}

export interface TerminalCommandOutcome {
  readonly status: "success" | "failure" | "unknown";
  readonly exitCode?: number;
}

export interface TerminalCompletion {
  readonly markerPath: string;
  readonly command: string;
}

interface PendingTerminalCompletion {
  readonly terminal: vscode.Terminal;
  readonly markerPath: string;
  readonly resolve: (result: TerminalExecutionResult) => void;
  timer: NodeJS.Timeout | undefined;
}

export interface TerminalSessionManagerOptions {
  readonly createCompletion?: (line: string) => Promise<TerminalCompletion>;
  readonly completionPollIntervalMs?: number;
}

export class TerminalSessionManager {
  private terminal: vscode.Terminal | undefined;
  private terminalCwd: string | undefined;
  private terminalProfile: TerminalProfile | undefined;
  private readonly terminalLogger = new TerminalLogger();
  private logTerminal: vscode.Terminal | undefined;
  private readonly createCompletion: (
    line: string,
  ) => Promise<TerminalCompletion>;
  private readonly completionPollIntervalMs: number;
  private readonly pendingCompletions = new Map<
    string,
    PendingTerminalCompletion
  >();

  constructor(options: TerminalSessionManagerOptions = {}) {
    this.createCompletion =
      options.createCompletion ?? createTerminalCompletion;
    this.completionPollIntervalMs = options.completionPollIntervalMs ?? 100;
  }

  terminalForFolder(folder: RepoWorkspaceFolder): vscode.Terminal {
    return this.terminalForFolderProfile(folder, { kind: "default" });
  }

  async terminalForRepoCommand(
    folder: RepoWorkspaceFolder,
    action: RepoCommandAction,
  ): Promise<vscode.Terminal> {
    if (!usesRuntimeTerminalPath(action)) {
      return this.terminalForFolderProfile(folder, { kind: "default" });
    }

    const terminalPath = await terminalPathEnvironmentForRepo(folder.fsPath);
    if (terminalPath.entries.length > 0) {
      this.logToTerminal(
        "context",
        `PATH += ${terminalPath.entries.join(
          process.platform === "win32" ? ";" : ":",
        )}`,
        folder,
      );
    }
    return this.terminalForFolderProfile(folder, {
      kind: "runtime",
      env: terminalPath.env,
      signature: terminalPath.entries.join("\0"),
    });
  }

  async executeInFreeCMTerminal(
    folder: RepoWorkspaceFolder,
    label: string,
    terminalFactory: () => vscode.Terminal | Promise<vscode.Terminal>,
    lines: readonly string[],
  ): Promise<TerminalCommandOutcome> {
    for (const shouldRetry of [true, false]) {
      try {
        const terminal = await terminalFactory();
        terminal.show();
        const line = terminalCommandSequence(lines);
        if (line === undefined) {
          return { status: "success", exitCode: 0 };
        }

        const shellIntegration = await this.waitForShellIntegration(terminal);
        const completion = await this.createCompletion(line);
        try {
          if (shellIntegration !== undefined) {
            await this.ensureTerminalCwd(shellIntegration, folder);
            shellIntegration.executeCommand(completion.command);
          } else {
            terminal.sendText(completion.command);
          }
        } catch (error) {
          void removeCompletionMarker(completion.markerPath);
          throw error;
        }
        const result = await this.waitForCompletion(
          completion.markerPath,
          terminal,
        );
        const outcome = terminalExecutionOutcome(result);
        this.logRepoCommandFinished(label, outcome);
        return outcome;
      } catch (error) {
        if (!shouldRetry || !isDisposedTerminalError(error)) {
          throw error;
        }
        this.clearTerminalReference();
        this.logToTerminal(
          "warning",
          "FreeCM terminal was already disposed; recreating it and retrying.",
          folder,
        );
      }
    }
    return { status: "unknown" };
  }

  terminalOutput(folder: RepoWorkspaceFolder): {
    log(level: TerminalLogLevel, value: string): void;
  } {
    return {
      log: (level, value) => {
        this.logToTerminal(level, value, folder);
      },
    };
  }

  logToTerminal(
    level: TerminalLogLevel,
    message: string,
    _folder?: RepoWorkspaceFolder,
  ): void {
    if (this.logTerminal === undefined) {
      this.logTerminal = vscode.window.createTerminal({
        name: LOG_TERMINAL_NAME,
        pty: this.terminalLogger,
      });
    }
    this.logTerminal.show(true);
    this.terminalLogger.log(level, message);
  }

  finishTerminalLogGroup(): void {
    this.terminalLogger.separator();
  }

  handleTerminalClosed(closedTerminal: vscode.Terminal): void {
    this.flushPendingCompletionsForTerminal(closedTerminal);
    if (closedTerminal === this.terminal) {
      this.terminal = undefined;
      this.terminalCwd = undefined;
      this.terminalProfile = undefined;
    }
    if (closedTerminal === this.logTerminal) {
      this.logTerminal = undefined;
    }
  }

  private waitForCompletion(
    markerPath: string,
    terminal: vscode.Terminal,
  ): Promise<TerminalExecutionResult> {
    return new Promise((resolve) => {
      this.pendingCompletions.set(markerPath, {
        terminal,
        markerPath,
        resolve,
        timer: undefined,
      });
      void this.pollCompletion(markerPath);
    });
  }

  private async pollCompletion(markerPath: string): Promise<void> {
    const entry = this.pendingCompletions.get(markerPath);
    if (entry === undefined) {
      return;
    }

    try {
      const exitCode = parseCompletionExitCode(
        await fs.readFile(markerPath, "utf8"),
      );
      if (exitCode !== undefined) {
        this.resolvePendingCompletion(markerPath, {
          exitCode,
          terminalClosed: false,
        });
        return;
      }
    } catch (error) {
      if (!isNodeErrorCode(error, "ENOENT")) {
        this.resolvePendingCompletion(markerPath, {
          exitCode: undefined,
          terminalClosed: false,
        });
        return;
      }
    }

    const pending = this.pendingCompletions.get(markerPath);
    if (pending !== undefined) {
      pending.timer = setTimeout(() => {
        void this.pollCompletion(markerPath);
      }, this.completionPollIntervalMs);
    }
  }

  private terminalForFolderProfile(
    folder: RepoWorkspaceFolder,
    profile: TerminalProfile,
  ): vscode.Terminal {
    if (
      this.terminal !== undefined &&
      this.terminalCwd === folder.fsPath &&
      terminalProfilesEqual(this.terminalProfile, profile)
    ) {
      return this.terminal;
    }

    if (this.terminal !== undefined) {
      this.flushPendingCompletionsForTerminal(this.terminal);
    }
    this.terminal?.dispose();
    this.terminal = vscode.window.createTerminal({
      name: TERMINAL_NAME,
      cwd: folder.fsPath,
      env: profile.env,
      ...terminalBootstrapOptions(),
    });
    this.terminalCwd = folder.fsPath;
    this.terminalProfile = profile;
    return this.terminal;
  }

  private clearTerminalReference(): void {
    const terminal = this.terminal;
    if (terminal !== undefined) {
      this.flushPendingCompletionsForTerminal(terminal);
    }
    this.terminal = undefined;
    this.terminalCwd = undefined;
    this.terminalProfile = undefined;
  }

  private flushPendingCompletionsForTerminal(
    terminal: vscode.Terminal,
  ): void {
    for (const [markerPath, entry] of Array.from(this.pendingCompletions)) {
      if (entry.terminal === terminal) {
        this.resolvePendingCompletion(markerPath, {
          exitCode: undefined,
          terminalClosed: true,
        });
      }
    }
  }

  private resolvePendingCompletion(
    markerPath: string,
    result: TerminalExecutionResult,
  ): void {
    const entry = this.pendingCompletions.get(markerPath);
    if (entry === undefined) {
      return;
    }
    this.pendingCompletions.delete(markerPath);
    if (entry.timer !== undefined) {
      clearTimeout(entry.timer);
    }
    void removeCompletionMarker(entry.markerPath);
    entry.resolve(result);
  }

  private logRepoCommandFinished(
    label: string,
    outcome: TerminalCommandOutcome,
  ): void {
    const level: TerminalLogLevel =
      outcome.status === "unknown"
        ? "info"
        : outcome.status === "success"
          ? "success"
          : "error";
    const suffix =
      outcome.exitCode === undefined ? "" : ` (exit ${outcome.exitCode})`;
    this.logToTerminal(level, `Finished ${label}${suffix}`);
    this.finishTerminalLogGroup();
  }

  private async waitForShellIntegration(
    terminal: vscode.Terminal,
    timeoutMs: number = 3000,
  ): Promise<vscode.TerminalShellIntegration | undefined> {
    if (terminal.shellIntegration !== undefined) {
      return terminal.shellIntegration;
    }
    return new Promise((resolve) => {
      const disposable = vscode.window.onDidChangeTerminalShellIntegration(
        (event) => {
          if (event.terminal !== terminal) {
            return;
          }
          clearTimeout(timer);
          disposable.dispose();
          resolve(event.shellIntegration);
        },
      );
      const timer = setTimeout(() => {
        disposable.dispose();
        resolve(terminal.shellIntegration);
      }, timeoutMs);
    });
  }

  private async ensureTerminalCwd(
    shellIntegration: vscode.TerminalShellIntegration,
    folder: RepoWorkspaceFolder,
  ): Promise<void> {
    const currentCwd = shellIntegration.cwd;
    if (
      currentCwd === undefined ||
      currentCwd.scheme !== "file" ||
      sameFilePath(currentCwd.fsPath, folder.fsPath)
    ) {
      return;
    }

    this.logToTerminal(
      "warning",
      `FreeCM terminal was in ${currentCwd.fsPath}; switching back to ${
        folder.fsPath
      }.`,
      folder,
    );
    const execution = shellIntegration.executeCommand("cd", [folder.fsPath]);
    await waitForTerminalExecutionEnd(execution, 3000);
  }
}

function terminalExecutionOutcome(
  result: TerminalExecutionResult,
): TerminalCommandOutcome {
  if (result.terminalClosed || result.exitCode === undefined) {
    return { status: "unknown" };
  }
  if (result.exitCode !== 0) {
    return { status: "failure", exitCode: result.exitCode };
  }
  return { status: "success", exitCode: 0 };
}

async function createTerminalCompletion(
  line: string,
): Promise<TerminalCompletion> {
  await fs.mkdir(COMMAND_COMPLETION_DIRECTORY, { recursive: true });
  const markerPath = path.join(
    COMMAND_COMPLETION_DIRECTORY,
    `${process.pid}-${randomUUID()}.status`,
  );
  return {
    markerPath,
    command: terminalCompletionCommand(line, markerPath),
  };
}

function parseCompletionExitCode(value: string): number | undefined {
  const normalized = value.trim();
  if (!/^\d+$/.test(normalized)) {
    return undefined;
  }
  const exitCode = Number(normalized);
  return Number.isSafeInteger(exitCode) ? exitCode : undefined;
}

async function removeCompletionMarker(
  markerPath: string,
): Promise<void> {
  try {
    await fs.rm(markerPath, { force: true });
  } catch {
    // Completion markers are temporary; cleanup must not hide command status.
  }
}

function isNodeErrorCode(error: unknown, code: string): boolean {
  return (error as NodeJS.ErrnoException | undefined)?.code === code;
}

export function sameFilePath(
  left: string,
  right: string,
  platform: string = process.platform,
): boolean {
  if (platform === "win32") {
    return (
      path.normalize(left).toLowerCase() === path.normalize(right).toLowerCase()
    );
  }
  return path.normalize(left) === path.normalize(right);
}

export { errorMessage, isDisposedTerminalError };
