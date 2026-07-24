import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { RepoCommandAction } from "../repoCommands";

export interface TerminalBootstrapOptions {
  readonly shellPath?: string;
  readonly shellArgs?: string[];
}

export type TerminalProfile =
  | {
      readonly kind: "default";
      readonly env?: undefined;
      readonly signature?: undefined;
    }
  | {
      readonly kind: "runtime";
      readonly env: Record<string, string> | undefined;
      readonly signature: string;
    };

export function usesRuntimeTerminalPath(action: RepoCommandAction): boolean {
  return action === "run" || action === "test" || action === "package";
}

export function terminalCommandSequence(
  lines: readonly string[],
  platform: string = process.platform,
): string | undefined {
  if (lines.length === 0) {
    return undefined;
  }
  if (lines.length === 1) {
    return lines[0];
  }
  if (platform !== "win32") {
    return lines.join(" && ");
  }

  let sequence = lines[lines.length - 1];
  for (let index = lines.length - 2; index >= 0; index -= 1) {
    sequence = `${lines[index]}; if ($?) { ${sequence} }`;
  }
  return sequence;
}

export function terminalCompletionCommand(
  line: string,
  completionPath: string,
  platform: string = process.platform,
): string {
  if (platform === "win32") {
    const quotedPath = `'${completionPath.replace(/'/g, "''")}'`;
    return [
      "$__freecm_exit = 0",
      `try { & { ${line} }; if ($?) { $__freecm_exit = 0 } elseif ($LASTEXITCODE -is [int]) { $__freecm_exit = [int]$LASTEXITCODE } else { $__freecm_exit = 1 } } catch { $__freecm_exit = 1 }`,
      `[System.IO.File]::WriteAllText(${quotedPath}, \"$__freecm_exit\`n\")`,
    ].join("; ");
  }

  const quotedPath = `'${completionPath.replace(/'/g, "'\\''")}'`;
  return [
    `if ( ${line} ); then __freecm_exit=0; else __freecm_exit=$?; fi`,
    `printf '%s\\n' \"$__freecm_exit\" > ${quotedPath}`,
  ].join("; ");
}

export function terminalBootstrapOptions(
  platform: string = process.platform,
  env: NodeJS.ProcessEnv = process.env,
  pathExists: (path: string) => boolean = fs.existsSync,
): TerminalBootstrapOptions {
  if (platform !== "win32") {
    return {};
  }
  return {
    shellPath: windowsPowerShellPath(env, pathExists),
    shellArgs: [
      "-NoExit",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      windowsSetenvBootstrapCommand(),
    ],
  };
}

function envValue(env: NodeJS.ProcessEnv, name: string): string | undefined {
  const key = Object.keys(env).find(
    (candidate) => candidate.toLowerCase() === name.toLowerCase(),
  );
  return key === undefined ? undefined : env[key];
}

function windowsPowerShellPath(
  env: NodeJS.ProcessEnv,
  pathExists: (path: string) => boolean,
): string {
  const pathValue = envValue(env, "PATH");
  for (const entry of pathValue?.split(";") ?? []) {
    if (entry.trim() === "") {
      continue;
    }
    const candidate = path.win32.join(entry, "pwsh.exe");
    if (pathExists(candidate)) {
      return "pwsh.exe";
    }
  }

  const programFiles = envValue(env, "ProgramFiles") ?? "C:\\Program Files";
  const installedPwsh = path.win32.join(
    programFiles,
    "PowerShell",
    "7",
    "pwsh.exe",
  );
  if (pathExists(installedPwsh)) {
    return installedPwsh;
  }

  return "powershell.exe";
}

export function windowsSetenvBootstrapCommand(): string {
  return `
$ErrorActionPreference = 'Stop'
$existingSetenv = Get-Command setenv -ErrorAction SilentlyContinue
if ($existingSetenv) {
  Write-Host 'FreeCM: running user setenv.' -ForegroundColor DarkCyan
  setenv
} else {
  function global:setenv {
    param(
      [ValidateSet('amd64', 'x86', 'arm64')]
      [string]$Arch = 'amd64',
      [ValidateSet('amd64', 'x86', 'arm64')]
      [string]$HostArch = 'amd64',
      [switch]$SkipOneApi,
      [string]$DevPath = ''
    )

    $env:NINJA_STATUS = '[%f/%t %es] '
    $originalLocation = Get-Location
    $vsDevShellPath = $null
    $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if (-not $programFilesX86) {
      $programFilesX86 = Join-Path -Path $env:SystemDrive -ChildPath 'Program Files (x86)'
    }
    $vswhere = Join-Path -Path $programFilesX86 -ChildPath 'Microsoft Visual Studio\\Installer\\vswhere.exe'
    if (Test-Path -Path $vswhere) {
      $installPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
      if ($LASTEXITCODE -eq 0 -and $installPath) {
        $candidate = Join-Path -Path $installPath -ChildPath 'Common7\\Tools\\Launch-VsDevShell.ps1'
        if (Test-Path -Path $candidate) {
          $vsDevShellPath = $candidate
        }
      }
    }

    if (-not $vsDevShellPath) {
      foreach ($version in @('18', '17')) {
        foreach ($edition in @('Enterprise', 'Professional', 'Community', 'BuildTools')) {
          $candidate = "C:\\Program Files\\Microsoft Visual Studio\\$version\\$edition\\Common7\\Tools\\Launch-VsDevShell.ps1"
          if (Test-Path -Path $candidate) {
            $vsDevShellPath = $candidate
            break
          }
        }
        if ($vsDevShellPath) {
          break
        }
      }
    }

    if (-not $vsDevShellPath) {
      throw 'Cannot find Launch-VsDevShell.ps1. Please install Visual Studio C++ Build Tools.'
    }

    try {
      & $vsDevShellPath -Arch $Arch -HostArch $HostArch | Out-Null
    } finally {
      if (Test-Path -LiteralPath $originalLocation.Path) {
        Set-Location -LiteralPath $originalLocation.Path
      }
    }
    Write-Host "FreeCM: loaded VS environment: $vsDevShellPath" -ForegroundColor Green
  }

  Write-Host 'FreeCM: defining fallback setenv.' -ForegroundColor DarkCyan
  setenv
}
`.trim();
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
  return errorMessage(error)
    .toLowerCase()
    .includes("terminal has already been disposed");
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
