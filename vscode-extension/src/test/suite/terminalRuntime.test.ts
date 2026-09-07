import * as assert from "assert";
import {
  createWindowsBootstrapWatch,
  terminalCommandSequence,
  terminalBootstrapOptions,
  WINDOWS_BOOTSTRAP_CAP_MS,
  WINDOWS_BOOTSTRAP_IDLE_MS,
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

  test("Windows bootstrap watch resolves when the matching shell command ends", async () => {
    const starts: Array<(event: { terminal: object }) => void> = [];
    const ends: Array<(event: { terminal: object }) => void> = [];
    const watch = createWindowsBootstrapWatch({
      onDidStartTerminalShellExecution: (listener) => {
        starts.push(listener);
        return { dispose() {} };
      },
      onDidEndTerminalShellExecution: (listener) => {
        ends.push(listener);
        return { dispose() {} };
      },
      delay: () => new Promise(() => undefined),
    });
    const terminal = {};

    watch.observe(terminal);
    starts[0]({ terminal });
    ends[0]({ terminal });
    await watch.ready;
  });

  test("Windows bootstrap watch resolves after idle when no shell command starts", async () => {
    let releaseIdle: (() => void) | undefined;
    const watch = createWindowsBootstrapWatch({
      onDidStartTerminalShellExecution: () => ({ dispose() {} }),
      onDidEndTerminalShellExecution: () => ({ dispose() {} }),
      delay: (ms) => {
        if (ms === WINDOWS_BOOTSTRAP_IDLE_MS) {
          return new Promise((resolve) => {
            releaseIdle = resolve;
          });
        }
        return new Promise(() => undefined);
      },
    });

    watch.observe({});
    assert.ok(releaseIdle);
    releaseIdle();
    await watch.ready;
  });

  test("Windows bootstrap watch ignores idle once a shell command has started", async () => {
    const starts: Array<(event: { terminal: object }) => void> = [];
    const ends: Array<(event: { terminal: object }) => void> = [];
    let releaseIdle: (() => void) | undefined;
    const watch = createWindowsBootstrapWatch({
      onDidStartTerminalShellExecution: (listener) => {
        starts.push(listener);
        return { dispose() {} };
      },
      onDidEndTerminalShellExecution: (listener) => {
        ends.push(listener);
        return { dispose() {} };
      },
      delay: (ms) => {
        if (ms === WINDOWS_BOOTSTRAP_IDLE_MS) {
          return new Promise((resolve) => {
            releaseIdle = resolve;
          });
        }
        if (ms === WINDOWS_BOOTSTRAP_CAP_MS) {
          return new Promise(() => undefined);
        }
        return Promise.resolve();
      },
    });
    const terminal = {};
    let resolved = false;
    void watch.ready.then(() => {
      resolved = true;
    });

    watch.observe(terminal);
    starts[0]({ terminal });
    assert.ok(releaseIdle);
    releaseIdle();
    await new Promise<void>((resolve) => setImmediate(resolve));
    assert.strictEqual(resolved, false);

    ends[0]({ terminal });
    await watch.ready;
    assert.strictEqual(resolved, true);
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
