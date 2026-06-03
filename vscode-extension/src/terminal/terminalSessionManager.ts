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
  terminalProfilesEqual,
  usesRuntimeTerminalPath,
  waitForTerminalExecutionEnd,
} from "./terminalRuntime";

const TERMINAL_NAME = "FreeCM";
const LOG_TERMINAL_NAME = "FreeCM Log";

export class TerminalSessionManager {
  private terminal: vscode.Terminal | undefined;
  private terminalCwd: string | undefined;
  private terminalProfile: TerminalProfile | undefined;
  private readonly terminalLogger = new TerminalLogger();
  private logTerminal: vscode.Terminal | undefined;
  private pendingRepoCommandLabel: string | undefined;
  private readonly pendingExecutions = new Map<
    vscode.TerminalShellExecution,
    {
      label: string;
      terminal: vscode.Terminal;
    }
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
  ): Promise<void> {
    for (const shouldRetry of [true, false]) {
      try {
        const terminal = await terminalFactory();
        terminal.show();
        const shellIntegration = await this.waitForShellIntegration(terminal);
        if (shellIntegration !== undefined) {
          let lastExecution: vscode.TerminalShellExecution | undefined;
          await this.ensureTerminalCwd(shellIntegration, folder);
          for (const line of lines) {
            lastExecution = shellIntegration.executeCommand(line);
          }
          if (lastExecution !== undefined) {
            this.pendingExecutions.set(lastExecution, { label, terminal });
          }
        } else {
          this.pendingRepoCommandLabel = label;
          for (const line of lines) {
            terminal.sendText(line);
          }
        }
        return;
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
    folder?: RepoWorkspaceFolder,
  ): void {
    if (folder !== undefined) {
      this.terminalForFolder(folder);
    }
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
    if (closedTerminal === this.terminal) {
      this.terminal = undefined;
      this.terminalCwd = undefined;
      this.terminalProfile = undefined;
      this.flushPendingExecutionsForTerminal(closedTerminal);
      this.flushPendingRepoCommand();
    }
    if (closedTerminal === this.logTerminal) {
      this.logTerminal = undefined;
    }
  }

  handleTerminalShellExecutionEnded(
    event: vscode.TerminalShellExecutionEndEvent,
  ): void {
    const entry = this.pendingExecutions.get(event.execution);
    if (entry === undefined) {
      return;
    }
    this.pendingExecutions.delete(event.execution);
    this.logRepoCommandFinished(entry.label, event.exitCode);
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
    }
    this.terminal?.dispose();
    this.terminal = vscode.window.createTerminal({
      name: TERMINAL_NAME,
      cwd: folder.fsPath,
      env: profile.env,
    });
    this.terminalCwd = folder.fsPath;
    this.terminalProfile = profile;
    return this.terminal;
  }

  private clearTerminalReference(): void {
    const terminal = this.terminal;
    if (terminal !== undefined) {
      for (const [execution, entry] of Array.from(this.pendingExecutions)) {
        if (entry.terminal === terminal) {
          this.pendingExecutions.delete(execution);
        }
      }
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
    this.logRepoCommandFinished(label, undefined);
  }

  private flushPendingExecutionsForTerminal(terminal: vscode.Terminal): void {
    for (const [execution, entry] of Array.from(this.pendingExecutions)) {
      if (entry.terminal === terminal) {
        this.pendingExecutions.delete(execution);
        this.logRepoCommandFinished(entry.label, undefined);
      }
    }
  }

  private logRepoCommandFinished(
    label: string,
    exitCode: number | undefined,
  ): void {
    const level: TerminalLogLevel =
      exitCode === undefined ? "info" : exitCode === 0 ? "success" : "error";
    const suffix = exitCode === undefined ? "" : ` (exit ${exitCode})`;
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
