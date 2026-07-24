import * as assert from "assert";
import { spawnSync } from "child_process";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import {
  terminalCommandSequence,
  terminalBootstrapOptions,
  terminalCompletionCommand,
  windowsSetenvBootstrapCommand,
} from "../../terminal/terminalRuntime";

suite("terminal runtime", () => {
  test("does not override shell on non-Windows platforms", () => {
    assert.deepStrictEqual(terminalBootstrapOptions("linux"), {});
    assert.deepStrictEqual(terminalBootstrapOptions("darwin"), {});
  });

  test("joins POSIX terminal steps into one fail-closed command", () => {
    assert.strictEqual(
      terminalCommandSequence(
        ["cmake --build --preset release", "./build/release/App"],
        "darwin",
      ),
      "cmake --build --preset release && ./build/release/App",
    );
  });

  test("guards PowerShell terminal steps without requiring chain operators", () => {
    assert.strictEqual(
      terminalCommandSequence(
        ["cmake --preset release", "cmake --build --preset release", ".\\App.exe"],
        "win32",
      ),
      "cmake --preset release; if ($?) { cmake --build --preset release; if ($?) { .\\App.exe } }",
    );
  });

  test("preserves empty and single-step terminal commands", () => {
    assert.strictEqual(terminalCommandSequence([], "linux"), undefined);
    assert.strictEqual(terminalCommandSequence(["./app"], "linux"), "./app");
  });

  test("records POSIX command completion with a quoted marker path", () => {
    assert.strictEqual(
      terminalCompletionCommand(
        "cmake --preset release",
        "/tmp/FreeCM's status",
        "darwin",
      ),
      "if ( cmake --preset release ); then __freecm_exit=0; else __freecm_exit=$?; fi; printf '%s\\n' \"$__freecm_exit\" > '/tmp/FreeCM'\\''s status'",
    );
  });

  test("records PowerShell command completion with a quoted marker path", () => {
    assert.strictEqual(
      terminalCompletionCommand(
        "cmake --preset release",
        "C:\\Temp\\FreeCM's status",
        "win32",
      ),
      "$__freecm_exit = 0; try { & { cmake --preset release }; if ($?) { $__freecm_exit = 0 } elseif ($LASTEXITCODE -is [int]) { $__freecm_exit = [int]$LASTEXITCODE } else { $__freecm_exit = 1 } } catch { $__freecm_exit = 1 }; [System.IO.File]::WriteAllText('C:\\Temp\\FreeCM''s status', \"$__freecm_exit`n\")",
    );
  });

  test("writes POSIX command exit statuses", async () => {
    if (process.platform === "win32") {
      return;
    }
    const directory = await fs.mkdtemp(
      path.join(os.tmpdir(), "freecm-terminal-runtime-"),
    );
    try {
      for (const [line, expectedExitCode] of [
        ["true", "0"],
        ["false", "1"],
      ]) {
        const markerPath = path.join(
          directory,
          `${expectedExitCode}.status`,
        );
        const result = spawnSync(
          "/bin/sh",
          [
            "-c",
            terminalCompletionCommand(line, markerPath, "linux"),
          ],
          { encoding: "utf8" },
        );
        assert.strictEqual(result.status, 0, result.stderr);
        assert.strictEqual(
          await fs.readFile(markerPath, "utf8"),
          `${expectedExitCode}\n`,
        );
      }
    } finally {
      await fs.rm(directory, { recursive: true, force: true });
    }
  });

  test("Windows terminal starts PowerShell setenv bootstrap", () => {
    const options = terminalBootstrapOptions("win32", { Path: "" }, () => false);

    assert.strictEqual(options.shellPath, "powershell.exe");
    assert.deepStrictEqual(options.shellArgs?.slice(0, 4), [
      "-NoExit",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
    ]);
    assert.strictEqual(options.shellArgs?.[4], windowsSetenvBootstrapCommand());
  });

  test("Windows terminal prefers PowerShell 7 when pwsh is available on PATH", () => {
    const options = terminalBootstrapOptions(
      "win32",
      { Path: "C:\\Program Files\\PowerShell\\7" },
      (candidate) =>
        candidate === "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
    );

    assert.strictEqual(options.shellPath, "pwsh.exe");
  });

  test("Windows terminal uses installed PowerShell 7 when it is not on PATH", () => {
    const pwshPath = "C:\\Program Files\\PowerShell\\7\\pwsh.exe";
    const options = terminalBootstrapOptions(
      "win32",
      { Path: "", ProgramFiles: "C:\\Program Files" },
      (candidate) => candidate === pwshPath,
    );

    assert.strictEqual(options.shellPath, pwshPath);
  });

  test("Windows bootstrap prefers user setenv before defining fallback", () => {
    const command = windowsSetenvBootstrapCommand();

    assert.match(command, /Get-Command setenv/);
    assert.match(command, /running user setenv/);
    assert.match(command, /function global:setenv/);
    assert.match(command, /Launch-VsDevShell\.ps1/);
    assert.match(command, /\[switch\]\$SkipOneApi/);
    assert.match(command, /setenv/);
  });

  test("Windows fallback setenv restores the original working directory", () => {
    const command = windowsSetenvBootstrapCommand();

    assert.match(command, /\$originalLocation = Get-Location/);
    assert.match(
      command,
      /Set-Location -LiteralPath \$originalLocation\.Path/,
    );
  });

  test("Windows fallback setenv keeps Program Files x86 path as one argument", () => {
    const command = windowsSetenvBootstrapCommand();

    assert.match(
      command,
      /\[Environment\]::GetEnvironmentVariable\('ProgramFiles\(x86\)'\)/,
    );
    assert.ok(
      command.includes(
        "$vswhere = Join-Path -Path $programFilesX86 -ChildPath 'Microsoft Visual Studio\\Installer\\vswhere.exe'",
      ),
    );
    assert.doesNotMatch(command, /Join-Path \$\{env:ProgramFiles\(x86\)\}/);
  });
});
