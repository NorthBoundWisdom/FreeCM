# FreeCM VS Code Extension

Run FreeCM source-root workflow commands from the VS Code status bar and
Workflow activity-bar panel.

Open the panel from the FreeCM activity-bar icon or the command palette entry
`FreeCM: Show Workflow Panel`. The panel is a packaged VS Code webview and does
not require a separate development server.

## Workspace Eligibility

FreeCM enables workflow controls independently when the current workspace folder
has the corresponding files or directories:

- `build/dependency_seed_repos/` for `Pull Seeds`
- `configs/source_root_workflow.py`
- `source_roots.lock.jsonc` or `source_roots.lock.jsonc.in`

Workflow buttons submit these commands to the integrated terminal named
`FreeCM`; the separate `FreeCM Log` terminal records delivery messages. Windows
uses `python`; macOS and Linux use `python3`:

```bash
python3 configs/source_root_workflow.py --init
python3 configs/source_root_workflow.py --update
```

Repositories that only provide `scripts/source_root_workflow.py` are not
supported.

FreeCM submits workflow and project commands to the integrated terminal without
tracking their completion. A one-step command is sent exactly as declared; for
example, `cmake --preset mac_clang_release` appears as that command rather than
an exit-code wrapper. Delivery is serialized per workspace, then the terminal
shell owns execution order. There are no completion files, polling loops, or
shell-integration events holding the controls disabled. `Ctrl+C` stops the
active terminal process without leaving FreeCM in a busy state.

This deliberately matches typing a command and pressing Enter in the terminal.
Most configure and build tools leave queued input for the shell. An interactive
program can consume its own standard input, so stop an interactive `Run`
command before submitting another command behind it.

The active lock `source_roots.lock.jsonc` takes precedence when present. The
template lock `source_roots.lock.jsonc.in` is the committed fallback used to
create the active lock before lock-mode edits.

Lock-mode commands use the same `.freecm.workspace.lock` directory lock as the
Python FreeCM workflow. This keeps extension writes from racing `--init`,
`--update`, materialization, or dependency pinning run from another terminal.
`Pin latest` releases that lock while it runs the offline Python `--update`
command, then reacquires it to pin or restore the active lock.

`Pull Seeds` holds the same workspace lock while it checks each direct child of
`build/dependency_seed_repos/` in name order. It ignores non-Git asset
directories, skips dirty repositories, and runs `git pull --rebase` for each
clean repository. A failed pull is reported without stopping the remaining
repositories; the action never creates seeds or changes dependency locks.

## Project Commands

The Workflow panel and status bar can expose repository-defined `Config`,
`Build`, `Run`, `Test`, and `Package` buttons when the workspace provides
`configs/freecm.commands.jsonc`. Commands are declared as argv arrays and run
from the repository root in the integrated terminal named `FreeCM`. This is the
canonical reference for the project-command manifest; the root README keeps a
short workflow overview.

Buttons are ordered as `Config`, `Build`, `Run`, `Test`, then `Package`.
Configuration is intentionally separate; build commands should not silently run
configuration first. `Config` is also the active context: it controls which
downstream variants are compatible and supplies their defaults. Submitting
Config records the current configuration signature so dependent commands can
be queued behind it without waiting for completion.

Use `command` + `args` for one terminal command, or `steps` for a small ordered
sequence:

```jsonc
{
  "version": 2,
  "commands": {
    "config": [
      {
        "id": "mac-config",
        "label": "Mac Config",
        "command": "cmake",
        "args": ["--preset", "mac_clang_release"],
        "platforms": ["darwin"],
        "default": true,
        "defaults": {
          "build": "mac-release",
          "package": "mac-package"
        },
        "readiness": {
          "inputs": ["CMakePresets.json", "source_roots.lock.jsonc"],
          "outputs": ["build/mac_clang_release/CMakeCache.txt"]
        }
      }
    ],
    "build": [
      {
        "id": "mac-release",
        "label": "Mac Release",
        "command": "cmake",
        "args": ["--build", "--preset", "mac_clang_release"],
        "configurations": ["mac-config"]
      }
    ],
    "run": [],
    "test": [],
    "package": [
      {
        "id": "mac-package",
        "label": "Mac Package",
        "command": "cmake",
        "args": ["--build", "--preset", "mac_clang_release", "--target", "package"],
        "configurations": ["mac-config"]
      }
    ]
  }
}
```

Only Config variants may declare `platforms`, `default`, `defaults`, and
`readiness`. Every downstream variant must name one or more Config IDs in
`configurations`; one variant can be shared across Configs. Each Config must
declare a default for every downstream action it exposes.

The active Config and downstream selections are stored per workspace.
Downstream selections are scoped to their Config, so switching Config restores
compatible choices instead of retaining an unrelated command.

After Config is delivered to the terminal, the extension records a submission
receipt for that Config. Its signature includes the Config command and the
contents of `readiness.inputs`. A missing or stale receipt shows `Needs Config`
and disables Build, Run, Test, and Package. Declared `readiness.outputs` are
reported while missing but do not block commands already queued behind Config.
FreeCM does not claim that Config succeeded and does not inspect its exit code.
Dependent actions never configure implicitly. Multi-step variants are submitted
as one fail-closed shell sequence.

Manifest version 1 is intentionally unsupported. Migrate downstream
`platforms` and `default` fields into Config-owned `defaults` plus each
downstream variant's explicit `configurations`.

`Package` entries should run the full packaging flow themselves. After the user
has already run FreeCM init/update, a package command should stay offline,
perform any required release build, archive/sign/stage the payload, and produce
the final distributable artifact such as a DMG, archive export, or portable zip.

`Run` should stay terminal-owned. On macOS, prefer launching the executable
inside an app bundle, for example
`./build/.../App.app/Contents/MacOS/App`, instead of `open build/.../App.app`.
Using `open` detaches from the terminal, so logs are not streamed and `Ctrl+C`
cannot stop the process.

Validate and preview a downstream manifest without opening VS Code:

```bash
cd /path/to/downstream
node FreeCM/vscode-extension/out/validateRepoCommands.js --preview .
```

The validator uses the same parser and terminal quoting as the extension. It
exits non-zero for invalid manifests and prints warnings for common detach
patterns.

## Development

```bash
npm ci
npm run compile
npm test
npm run validate:commands
```

Run `npm audit --omit=optional` when extension dependencies change. For a
release or package-affecting change, also run `npm run package` and
`npm run smoke:vsix`; see `docs/release-process.md` in the FreeCM source
repository for the complete gate. Use VS Code's extension host launch flow to
try the extension against a migrated downstream repository.
