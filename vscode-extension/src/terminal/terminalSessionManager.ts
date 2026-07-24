import * as path from "path";
import * as vscode from "vscode";

import { TerminalLogger, TerminalLogLevel } from "../terminalLogger";
import { terminalPathEnvironmentForRepo } from "../terminalPath";
import { RepoWorkspaceFolder } from "../workspaceDiscovery";

import {
  errorMessage,
  isDisposedTerminalError,
  terminalBootstrapOptions,
  terminalCommandSequence,
} from "./terminalRuntime";

const TERMINAL_NAME = "FreeCM";
const LOG_TERMINAL_NAME = "FreeCM Log";

interface ManagedTerminal {
  readonly terminal: vscode.Terminal;
  readonly folderPath: string;
}

export class TerminalSessionManager {
  private readonly terminals: ManagedTerminal[] = [];
  private readonly terminalLogger = new TerminalLogger();
  private readonly dispatchQueues = new Map<string, Promise<void>>();
  private logTerminal: vscode.Terminal | undefined;

  async terminalForFolder(
    folder: RepoWorkspaceFolder,
  ): Promise<vscode.Terminal> {
    const existing = this.findTerminal(folder.fsPath);
    if (existing !== undefined) {
      return existing;
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
    return this.createTerminal(folder, terminalPath.env);
  }

  terminalForRepoCommand(
    folder: RepoWorkspaceFolder,
  ): Promise<vscode.Terminal> {
    return this.terminalForFolder(folder);
  }

  queueInFreeCMTerminal(
    folder: RepoWorkspaceFolder,
    terminalFactory: () => vscode.Terminal | Promise<vscode.Terminal>,
    lines: readonly string[],
  ): Promise<void> {
    const previous =
      this.dispatchQueues.get(folder.fsPath) ?? Promise.resolve();
    const queued = previous
      .catch(() => undefined)
      .then(() => this.sendToTerminal(folder, terminalFactory, lines));
    this.dispatchQueues.set(folder.fsPath, queued);
    void queued.then(
      () => this.clearDispatchQueue(folder.fsPath, queued),
      () => this.clearDispatchQueue(folder.fsPath, queued),
    );
    return queued;
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
    for (let index = this.terminals.length - 1; index >= 0; index -= 1) {
      if (this.terminals[index].terminal === closedTerminal) {
        this.terminals.splice(index, 1);
      }
    }
    if (closedTerminal === this.logTerminal) {
      this.logTerminal = undefined;
    }
  }

  private async sendToTerminal(
    folder: RepoWorkspaceFolder,
    terminalFactory: () => vscode.Terminal | Promise<vscode.Terminal>,
    lines: readonly string[],
  ): Promise<void> {
    const line = terminalCommandSequence(lines);
    if (line === undefined) {
      return;
    }

    for (const shouldRetry of [true, false]) {
      let terminal: vscode.Terminal | undefined;
      try {
        terminal = await terminalFactory();
        terminal.show();
        // sendText deliberately mirrors typing a command and pressing Enter.
        // Shell-integration executeCommand would interrupt an active command.
        terminal.sendText(line);
        return;
      } catch (error) {
        if (!shouldRetry || !isDisposedTerminalError(error)) {
          throw error;
        }
        if (terminal !== undefined) {
          this.clearTerminalReference(terminal);
        }
        this.logToTerminal(
          "warning",
          "FreeCM terminal was already disposed; recreating it and retrying.",
          folder,
        );
      }
    }
  }

  private findTerminal(folderPath: string): vscode.Terminal | undefined {
    return this.terminals.find(
      (entry) => sameFilePath(entry.folderPath, folderPath),
    )?.terminal;
  }

  private createTerminal(
    folder: RepoWorkspaceFolder,
    env: Record<string, string> | undefined,
  ): vscode.Terminal {
    const terminal = vscode.window.createTerminal({
      name: TERMINAL_NAME,
      cwd: folder.fsPath,
      env,
      ...terminalBootstrapOptions(),
    });
    this.terminals.push({
      terminal,
      folderPath: folder.fsPath,
    });
    return terminal;
  }

  private clearTerminalReference(terminal: vscode.Terminal): void {
    const index = this.terminals.findIndex(
      (entry) => entry.terminal === terminal,
    );
    if (index >= 0) {
      this.terminals.splice(index, 1);
    }
  }

  private clearDispatchQueue(
    folderPath: string,
    queued: Promise<void>,
  ): void {
    if (this.dispatchQueues.get(folderPath) === queued) {
      this.dispatchQueues.delete(folderPath);
    }
  }
}

export function sameFilePath(
  left: string,
  right: string,
  platform: string = process.platform,
): boolean {
  if (platform === "win32") {
    return (
      path.win32.normalize(left).toLowerCase() ===
      path.win32.normalize(right).toLowerCase()
    );
  }
  return path.normalize(left) === path.normalize(right);
}

export { errorMessage, isDisposedTerminalError };
