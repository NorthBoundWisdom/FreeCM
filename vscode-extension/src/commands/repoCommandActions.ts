import { RepoCommandAction } from "../repoCommands";
import {
  RepoCommandSelectCommand,
  WorkflowCommand,
} from "../webview/messageProtocol";

export const PRIMARY_REPO_COMMAND_ACTIONS: readonly RepoCommandAction[] = [
  "config",
  "build",
  "run",
];

export function isRepoCommandAction(
  command: WorkflowCommand,
): command is RepoCommandAction {
  return (
    command === "config" ||
    command === "build" ||
    command === "run" ||
    command === "test" ||
    command === "package"
  );
}

export function isRepoCommandSelectCommand(
  command: WorkflowCommand,
): command is RepoCommandSelectCommand {
  return (
    command === "selectConfig" ||
    command === "selectBuild" ||
    command === "selectTest" ||
    command === "selectRun" ||
    command === "selectPackage"
  );
}

export function repoCommandActionForSelectCommand(
  command: RepoCommandSelectCommand,
): RepoCommandAction {
  if (command === "selectConfig") {
    return "config";
  }
  if (command === "selectBuild") {
    return "build";
  }
  if (command === "selectTest") {
    return "test";
  }
  if (command === "selectRun") {
    return "run";
  }
  return "package";
}

export function statusBarIconForRepoAction(action: RepoCommandAction): string {
  if (action === "config") {
    return "$(gear)";
  }
  if (action === "build") {
    return "$(tools)";
  }
  if (action === "test") {
    return "$(beaker)";
  }
  if (action === "run") {
    return "$(play)";
  }
  return "$(package)";
}

export function webviewIconForRepoAction(action: RepoCommandAction): string {
  if (action === "config") {
    return "⚙";
  }
  if (action === "build") {
    return "⚒";
  }
  if (action === "run") {
    return "▶";
  }
  if (action === "test") {
    return "⚗";
  }
  return "□";
}

export function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
