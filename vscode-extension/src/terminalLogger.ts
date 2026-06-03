import * as vscode from "vscode";

export type TerminalLogLevel =
  | "info"
  | "success"
  | "warning"
  | "error"
  | "context";

const ANSI_COLORS: Record<TerminalLogLevel, string> = {
  info: "36",
  success: "32",
  warning: "33",
  error: "31",
  context: "90",
};

export class TerminalLogger implements vscode.Pseudoterminal {
  private readonly writeEmitter = new vscode.EventEmitter<string>();
  private hasPendingGroup = false;
  readonly onDidWrite: vscode.Event<string> = this.writeEmitter.event;

  open(): void {
    this.writeEmitter.fire("\x1b[36m[FreeCM]\x1b[0m log terminal ready\r\n");
  }

  close(): void {
    // No resources to release.
  }

  log(level: TerminalLogLevel, message: string): void {
    for (const line of terminalLogLines(level, message)) {
      this.writeEmitter.fire(`${line}\r\n`);
      this.hasPendingGroup = true;
    }
  }

  separator(): void {
    if (!this.hasPendingGroup) {
      return;
    }
    this.writeEmitter.fire(`${terminalSeparatorLine()}\r\n`);
    this.hasPendingGroup = false;
  }
}

export function terminalLogLines(
  level: TerminalLogLevel,
  message: string,
): string[] {
  return splitLogLines(message).map((line) =>
    formatTerminalLogLine(level, line),
  );
}

function formatTerminalLogLine(level: TerminalLogLevel, line: string): string {
  return `\x1b[${ANSI_COLORS[level]}m[FreeCM]\x1b[0m ${line}`;
}

export function terminalSeparatorLine(): string {
  return `\x1b[90m${"-".repeat(72)}\x1b[0m`;
}

function splitLogLines(message: string): string[] {
  const lines = message.split(/\r?\n/);
  return lines.length === 0 ? [""] : lines;
}
