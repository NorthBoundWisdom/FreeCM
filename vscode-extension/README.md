# FreeCM VS Code Extension

Run FreeCM source-root workflow commands from the VS Code status bar and
Workflow activity-bar panel.

## Workspace Eligibility

FreeCM activates workflow controls when the current workspace folder has:

- `FreeCM/`
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

## Project Commands

The Workflow panel and status bar can expose repository-defined `Config`,
`Build`, `Run`, `Test`, and `Package` buttons when the workspace provides
`configs/freecm.commands.jsonc`. Commands are declared as argv arrays and run
from the repository root in the integrated terminal named `FreeCM`.

Use `command` + `args` for one terminal command, or `steps` for a small ordered
sequence:

```jsonc
{
  "version": 1,
  "commands": {
    "config": [
      {
        "id": "mac-config",
        "label": "Mac Config",
        "command": "cmake",
        "args": ["--preset", "mac_clang_release"],
        "platforms": ["darwin"],
        "default": true
      }
    ],
    "build": [
      {
        "id": "mac-release",
        "label": "Mac Release",
        "command": "cmake",
        "args": ["--build", "--preset", "mac_clang_release"],
        "platforms": ["darwin"],
        "default": true
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
        "platforms": ["darwin"]
      }
    ]
  }
}
```

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

Use VS Code's extension host launch flow to try the extension against a
migrated downstream repository.
