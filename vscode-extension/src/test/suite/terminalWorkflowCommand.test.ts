import * as assert from "assert";

import { terminalWorkflowCommand } from "../../terminal/terminalWorkflowCommand";

suite("terminal workflow command", () => {
  test("uses the extension host runtime without changing the POSIX shell environment", () => {
    assert.strictEqual(
      terminalWorkflowCommand(
        "manual-dependency",
        "/repo/Sample App",
        ["Lib A"],
        {
          platform: "darwin",
          executablePath: "/Applications/Visual Studio Code.app/Code Helper",
          runnerPath: "/extension/out/terminalWorkflowCli.js",
        },
      ),
      "ELECTRON_RUN_AS_NODE=1 '/Applications/Visual Studio Code.app/Code Helper' /extension/out/terminalWorkflowCli.js manual-dependency '/repo/Sample App' 'Lib A'",
    );
  });

  test("temporarily scopes Electron node mode in PowerShell", () => {
    const command = terminalWorkflowCommand(
      "pull-seeds",
      "C:\\Workspaces\\Sample App",
      [],
      {
        platform: "win32",
        executablePath: "C:\\Program Files\\Microsoft VS Code\\Code.exe",
        runnerPath: "C:\\Extensions\\FreeCM\\out\\terminalWorkflowCli.js",
      },
    );

    assert.ok(command.includes("Test-Path Env:ELECTRON_RUN_AS_NODE"));
    assert.ok(command.includes("$env:ELECTRON_RUN_AS_NODE = '1'"));
    assert.ok(
      command.includes(
        "& 'C:\\Program Files\\Microsoft VS Code\\Code.exe' " +
          "'C:\\Extensions\\FreeCM\\out\\terminalWorkflowCli.js' 'pull-seeds' " +
          "'C:\\Workspaces\\Sample App'",
      ),
    );
    assert.ok(
      command.includes(
        "Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue",
      ),
    );
  });

  test("quotes apostrophes in runner arguments", () => {
    assert.ok(
      terminalWorkflowCommand(
        "manual-dependency",
        "/repo/Host",
        ["Lib'A"],
        {
          platform: "linux",
          executablePath: "/usr/bin/node",
          runnerPath: "/tmp/FreeCM's runner.js",
        },
      ).includes(
        "'/tmp/FreeCM'\\''s runner.js' manual-dependency /repo/Host 'Lib'\\''A'",
      ),
    );
  });
});
