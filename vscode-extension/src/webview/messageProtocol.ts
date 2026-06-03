import { RepoCommandAction } from "../repoCommands";

export type LockWorkflowCommand =
  | "usePinned"
  | "pinLatest"
  | "manualAll"
  | "updateUsed";
export type MaintenanceCommand =
  | "cleanBuild"
  | "countCode"
  | "changeCountPath"
  | "resetCountPath"
  | "saveCountExcludePaths";
export type PullCommand = "pull" | "pullFreeCM";
export type RepoCommandSelectCommand =
  | "selectConfig"
  | "selectBuild"
  | "selectTest"
  | "selectRun"
  | "selectPackage";

export type WorkflowCommand =
  | "init"
  | "update"
  | PullCommand
  | LockWorkflowCommand
  | MaintenanceCommand
  | RepoCommandAction
  | RepoCommandSelectCommand;

export type WorkflowMessage =
  | { readonly command: Exclude<WorkflowCommand, "saveCountExcludePaths"> }
  | { readonly command: "saveCountExcludePaths"; readonly value: string };

const WORKFLOW_COMMANDS = new Set<string>([
  "init",
  "update",
  "pull",
  "pullFreeCM",
  "usePinned",
  "pinLatest",
  "manualAll",
  "updateUsed",
  "cleanBuild",
  "countCode",
  "changeCountPath",
  "resetCountPath",
  "saveCountExcludePaths",
  "config",
  "build",
  "test",
  "run",
  "package",
  "selectConfig",
  "selectBuild",
  "selectTest",
  "selectRun",
  "selectPackage",
]);

export function isWorkflowMessage(value: unknown): value is WorkflowMessage {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const command = (value as { command?: unknown }).command;
  if (typeof command !== "string" || !WORKFLOW_COMMANDS.has(command)) {
    return false;
  }
  if (command === "saveCountExcludePaths") {
    return typeof (value as { value?: unknown }).value === "string";
  }
  return true;
}
