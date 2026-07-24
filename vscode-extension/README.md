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

Workflow buttons run these commands through the FreeCM log terminal. Windows
uses `python`; macOS and Linux use `python3`:

```bash
python3 configs/source_root_workflow.py --init
python3 configs/source_root_workflow.py --update
```

Repositories that only provide `scripts/source_root_workflow.py` are not
supported.

When shell integration is available, FreeCM keeps workflow and project-command
controls disabled until the active terminal execution ends. Multi-step commands
run one step at a time, so launching a later step cannot interrupt an active
step with `Ctrl+C`.

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
from the repository root in the integrated terminal named `FreeCM`.

Buttons are ordered as `Config`, `Build`, `Run`, `Test`, then `Package`.
Configuration is intentionally separate; build commands should not silently run
configuration first. `Config` is also the active context: it controls which
downstream variants are compatible, supplies their defaults, and must complete
successfully before they can run.

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
        "command": "python3",
        "args": ["configs/ios_workflow.py", "package", "--configuration", "Release"],
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

After Config exits successfully, the extension records a readiness receipt for
that Config. Its signature includes the Config command and the contents of
`readiness.inputs`; every `readiness.outputs` path must exist. A missing or
stale receipt shows `Needs Config` and disables Build, Run, Test, and Package.
Those actions never configure implicitly. If terminal shell integration cannot
report completion, the extension leaves the Config unready. Observable
multi-step commands stop at the first failing step.

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
npm audit --omit=optional
npm run package
npm run validate:commands
```

Use VS Code's extension host launch flow to try the extension against a
migrated downstream repository.
