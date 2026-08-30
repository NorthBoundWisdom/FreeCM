import * as path from "path";

export type TerminalWorkflowAction =
  | "pull"
  | "pull-seeds"
  | "use-pinned"
  | "pin-latest"
  | "manual-all"
  | "update-used"
  | "apply-active-dependency-to-sample"
  | "manual-dependency"
  | "restore-dependency-pin"
  | "clean-build";

export interface TerminalWorkflowCommandOptions {
  readonly platform?: string;
  readonly executablePath?: string;
  readonly runnerPath?: string;
}

export function terminalWorkflowCommand(
  action: TerminalWorkflowAction,
  repoRoot: string,
  args: readonly string[] = [],
  options: TerminalWorkflowCommandOptions = {},
): string {
  const platform = options.platform ?? process.platform;
  const executablePath = options.executablePath ?? process.execPath;
  const runnerPath =
    options.runnerPath ?? path.join(__dirname, "..", "terminalWorkflowCli.js");
  const argv = [executablePath, runnerPath, action, repoRoot, ...args];

  if (platform !== "win32") {
    return `ELECTRON_RUN_AS_NODE=1 ${argv.map(posixShellQuote).join(" ")}`;
  }

  const invocation = `& ${argv.map(powerShellQuote).join(" ")}`;
  return [
    "& {",
    "$freecmHadElectronRunAsNode = Test-Path Env:ELECTRON_RUN_AS_NODE;",
    "$freecmPreviousElectronRunAsNode = $env:ELECTRON_RUN_AS_NODE;",
    "$freecmExitCode = 0;",
    "try {",
    "$env:ELECTRON_RUN_AS_NODE = '1';",
    `${invocation};`,
    "$freecmExitCode = $LASTEXITCODE;",
    "} finally {",
    "if ($freecmHadElectronRunAsNode) {",
    "$env:ELECTRON_RUN_AS_NODE = $freecmPreviousElectronRunAsNode;",
    "} else {",
    "Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue;",
    "}",
    "}",
    "if ($freecmExitCode -ne 0) {",
    'Write-Error "FreeCM command failed with exit code $freecmExitCode";',
    "}",
    "}",
  ].join(" ");
}

function posixShellQuote(value: string): string {
  if (value.length > 0 && /^[A-Za-z0-9_./:=@%+-]+$/.test(value)) {
    return value;
  }
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

function powerShellQuote(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}
