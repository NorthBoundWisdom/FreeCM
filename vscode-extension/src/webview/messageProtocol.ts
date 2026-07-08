import { RepoCommandAction } from "../repoCommands";

export type LockWorkflowCommand =
  | "usePinned"
  | "pinLatest"
  | "manualAll"
  | "updateUsed";
export type DependencyWorkflowCommand =
  | "applyActiveDependencyToSample"
  | "manualDependency"
  | "restoreDependencyPin";
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
  | DependencyWorkflowCommand
  | MaintenanceCommand
  | RepoCommandAction
  | RepoCommandSelectCommand;

export type WorkflowMessage =
  | {
      readonly command: Exclude<
        WorkflowCommand,
        "saveCountExcludePaths" | DependencyWorkflowCommand
      >;
    }
  | {
      readonly command: DependencyWorkflowCommand;
      readonly dependency: string;
    }
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
  "applyActiveDependencyToSample",
  "manualDependency",
  "restoreDependencyPin",
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
  if (
    command === "applyActiveDependencyToSample" ||
    command === "manualDependency" ||
    command === "restoreDependencyPin"
  ) {
    const dependency = (value as { dependency?: unknown }).dependency;
    return typeof dependency === "string" && isSafeDependencyName(dependency);
  }
  return true;
}

function isSafeDependencyName(name: string): boolean {
  return (
    /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name) &&
    name !== "." &&
    name !== ".." &&
    !name.includes("/") &&
    !name.includes("\\") &&
    !name.split(".").includes("..")
  );
}
