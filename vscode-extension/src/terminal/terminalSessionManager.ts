import * as path from "path";
import * as vscode from "vscode";

import { RepoCommandAction } from "../repoCommands";
import { TerminalLogger, TerminalLogLevel } from "../terminalLogger";
import { terminalPathEnvironmentForRepo } from "../terminalPath";
import { RepoWorkspaceFolder } from "../workspaceDiscovery";

import {
  errorMessage,
  isDisposedTerminalError,
  TerminalProfile,
  terminalBootstrapOptions,
  terminalProfilesEqual,
  usesRuntimeTerminalPath,
  waitForTerminalExecutionEnd,
} from "./terminalRuntime";

const TERMINAL_NAME = "FreeCM";
const LOG_TERMINAL_NAME = "FreeCM Log";

interface TerminalExecutionResult {
  readonly exitCode: number | undefined;
  readonly terminalClosed: boolean;
}

export interface TerminalCommandOutcome {
  readonly status: "success" | "failure" | "unknown";
  readonly exitCode?: number;
}

interface PendingTerminalExecution {
  readonly terminal: vscode.Terminal;
  readonly resolve: (result: TerminalExecutionResult) => void;
}

export class TerminalSessionManager {
  private terminal: vscode.Terminal | undefined;
  private terminalCwd: string | undefined;
  private terminalProfile: TerminalProfile | undefined;
  private readonly terminalLogger = new TerminalLogger();
  private logTerminal: vscode.Terminal | undefined;
  private pendingRepoCommandLabel: string | undefined;
  private readonly pendingExecutions = new Map<
    vscode.TerminalShellExecution,
    PendingTerminalExecution
  >();

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
        const shellIntegration = await this.waitForShellIntegration(terminal);
        if (shellIntegration !== undefined) {
          await this.ensureTerminalCwd(shellIntegration, folder);
          if (lines.length === 0) {
            return { status: "success", exitCode: 0 };
          }

          for (const line of lines) {
            const execution = shellIntegration.executeCommand(line);
            const result = await this.waitForTerminalExecution(
              execution,
              terminal,
            );
            if (result.terminalClosed || result.exitCode === undefined) {
              const outcome: TerminalCommandOutcome = { status: "unknown" };
              this.logRepoCommandFinished(label, outcome);
              return outcome;
            }
            if (result.exitCode !== 0) {
              const outcome: TerminalCommandOutcome = {
                status: "failure",
                exitCode: result.exitCode,
              };
              this.logRepoCommandFinished(label, outcome);
              return outcome;
            }
          }
          const outcome: TerminalCommandOutcome = {
            status: "success",
            exitCode: 0,
          };
          this.logRepoCommandFinished(label, outcome);
          return outcome;
        } else {
          this.pendingRepoCommandLabel = label;
          for (const line of lines) {
            terminal.sendText(line);
          }
          return { status: "unknown" };
        }
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
    this.flushPendingExecutionsForTerminal(closedTerminal);
    if (closedTerminal === this.terminal) {
      this.terminal = undefined;
      this.terminalCwd = undefined;
      this.terminalProfile = undefined;
      this.flushPendingRepoCommand();
    }
    if (closedTerminal === this.logTerminal) {
      this.logTerminal = undefined;
    }
  }

  handleTerminalShellExecutionEnded(
    event: vscode.TerminalShellExecutionEndEvent,
  ): void {
    this.resolvePendingExecution(event.execution, {
      exitCode: event.exitCode,
      terminalClosed: false,
    });
  }

  private waitForTerminalExecution(
    execution: vscode.TerminalShellExecution,
    terminal: vscode.Terminal,
  ): Promise<TerminalExecutionResult> {
    return new Promise((resolve) => {
      this.pendingExecutions.set(execution, { terminal, resolve });
      void this.resolveWhenExecutionOutputEnds(execution);
    });
  }

  private async resolveWhenExecutionOutputEnds(
    execution: vscode.TerminalShellExecution,
  ): Promise<void> {
    try {
      for await (const _chunk of execution.read()) {
        // Retain a completion signal when VS Code misses the shell-execution
        // end event; reading does not change the visible terminal output.
      }
    } catch {
      return;
    }
    this.resolvePendingExecution(execution, {
      exitCode: undefined,
      terminalClosed: false,
    });
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
      this.flushPendingRepoCommand();
      this.flushPendingExecutionsForTerminal(this.terminal);
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
      this.flushPendingExecutionsForTerminal(terminal);
    }
    this.terminal = undefined;
    this.terminalCwd = undefined;
    this.terminalProfile = undefined;
    this.pendingRepoCommandLabel = undefined;
  }

  private flushPendingRepoCommand(): void {
    if (this.pendingRepoCommandLabel === undefined) {
      return;
    }
    const label = this.pendingRepoCommandLabel;
    this.pendingRepoCommandLabel = undefined;
    this.logRepoCommandFinished(label, { status: "unknown" });
  }

  private flushPendingExecutionsForTerminal(terminal: vscode.Terminal): void {
    for (const [execution, entry] of Array.from(this.pendingExecutions)) {
      if (entry.terminal === terminal) {
        this.resolvePendingExecution(execution, {
          exitCode: undefined,
          terminalClosed: true,
        });
      }
    }
  }

  private resolvePendingExecution(
    execution: vscode.TerminalShellExecution,
    result: TerminalExecutionResult,
  ): void {
    const entry = this.pendingExecutions.get(execution);
    if (entry === undefined) {
      return;
    }
    this.pendingExecutions.delete(execution);
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
