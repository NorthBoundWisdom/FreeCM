export type WorkflowFlag = "--init" | "--update";

export const WORKFLOW_SCRIPT = "configs/source_root_workflow.py";

export function pythonCommandForPlatform(platform: string = process.platform): string {
  return platform === "win32" ? "python" : "python3";
}

export function workflowInvocation(
  flag: WorkflowFlag,
  platform: string = process.platform,
): { command: string; args: readonly string[] } {
  return {
    command: pythonCommandForPlatform(platform),
    args: [WORKFLOW_SCRIPT, flag],
  };
}

export function workflowTerminalCommand(
  flag: WorkflowFlag,
  platform: string = process.platform,
): string {
  const invocation = workflowInvocation(flag, platform);
  return [invocation.command, ...invocation.args].join(" ");
}
