import { RepoCommandAction } from "../repoCommands";

export type LockWorkflowCommand = "usePinned" | "pinLatest" | "manualAll" | "updateUsed";
export type MaintenanceCommand = "cleanBuild" | "countCode" | "changeCountPath" | "resetCountPath";
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

export interface WorkflowMessage {
  readonly command: WorkflowCommand;
}

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
  return typeof command === "string" && WORKFLOW_COMMANDS.has(command);
}
