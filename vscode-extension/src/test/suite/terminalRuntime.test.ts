import * as assert from "assert";
import {
  terminalBootstrapOptions,
  windowsSetenvBootstrapCommand,
} from "../../terminal/terminalRuntime";

suite("terminal runtime", () => {
  test("does not override shell on non-Windows platforms", () => {
    assert.deepStrictEqual(terminalBootstrapOptions("linux"), {});
    assert.deepStrictEqual(terminalBootstrapOptions("darwin"), {});
  });

  test("Windows terminal starts PowerShell setenv bootstrap", () => {
    const options = terminalBootstrapOptions("win32");

    assert.strictEqual(options.shellPath, "powershell.exe");
    assert.deepStrictEqual(options.shellArgs?.slice(0, 4), [
      "-NoExit",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
    ]);
    assert.strictEqual(options.shellArgs?.[4], windowsSetenvBootstrapCommand());
  });

  test("Windows bootstrap prefers user setenv before defining fallback", () => {
    const command = windowsSetenvBootstrapCommand();

    assert.match(command, /Get-Command setenv/);
    assert.match(command, /running user setenv/);
    assert.match(command, /function global:setenv/);
    assert.match(command, /Launch-VsDevShell\.ps1/);
    assert.match(command, /setenv/);
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
