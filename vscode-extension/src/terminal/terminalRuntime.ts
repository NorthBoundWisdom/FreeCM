import * as vscode from "vscode";
import { RepoCommandAction } from "../repoCommands";

export type TerminalProfile =
  | { readonly kind: "default"; readonly env?: undefined; readonly signature?: undefined }
  | { readonly kind: "runtime"; readonly env: Record<string, string> | undefined; readonly signature: string };

export function usesRuntimeTerminalPath(action: RepoCommandAction): boolean {
  return action === "run" || action === "test" || action === "package";
}

export function terminalProfilesEqual(
  left: TerminalProfile | undefined,
  right: TerminalProfile,
): boolean {
  return (
    left !== undefined &&
    left.kind === right.kind &&
    left.signature === right.signature
  );
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function isDisposedTerminalError(error: unknown): boolean {
  return errorMessage(error).toLowerCase().includes("terminal has already been disposed");
}

export async function waitForTerminalExecutionEnd(
  execution: vscode.TerminalShellExecution,
  timeoutMs: number,
): Promise<number | undefined> {
  return await new Promise((resolve) => {
    const disposable = vscode.window.onDidEndTerminalShellExecution((event) => {
      if (event.execution !== execution) {
        return;
      }
      clearTimeout(timer);
      disposable.dispose();
      resolve(event.exitCode);
    });
    const timer = setTimeout(() => {
      disposable.dispose();
      resolve(undefined);
    }, timeoutMs);
  });
}
