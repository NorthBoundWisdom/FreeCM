# VS Code Extension MVP

## Goal

Build a small VS Code extension for repositories that use FreeCM source-root
dependency management. V1 exposes the host repository workflow from the VS Code
status bar so developers can run the two standard lifecycle commands without
leaving the editor. Windows uses `python`; macOS and Linux use `python3`:

- `python3 configs/source_root_workflow.py --init`
- `python3 configs/source_root_workflow.py --update`
- `python configs\source_root_workflow.py --init`
- `python configs\source_root_workflow.py --update`

V1 intentionally supports only host repositories that provide
`configs/source_root_workflow.py`. Repositories that still use
`scripts/source_root_workflow.py` must migrate before using the extension.

## Repository Detection

The extension is active for a workspace folder only when all of these files are
present at the folder root:

- `FreeCM/`
- `source_roots.lock.jsonc.in` or `source_roots.lock.jsonc`
- `configs/source_root_workflow.py`

For multi-root workspaces, commands resolve the target folder in this order:

1. the workspace folder containing the active editor file;
2. the single eligible workspace folder, when exactly one is detected;
3. otherwise, show a Quick Pick of eligible folder names.

If no eligible folder is detected, the status bar items stay hidden and commands
show a short warning.

## Status Bar UX

V1 contributes two left-side status bar items:

- `Init`: runs `--init`
- `Update`: runs `--update`

Each item has a tooltip that includes the resolved script path for the current
target folder. While a command is running, both items are disabled or replaced
with a running state so duplicate workflow processes are not started from the
extension.

## Command Execution

Commands run in a VS Code integrated terminal named `FreeCM`. The
terminal working directory is the selected workspace folder and the command is
sent with the platform Python launcher:

```bash
python3 configs/source_root_workflow.py --init
python3 configs/source_root_workflow.py --update
```

```powershell
python configs\source_root_workflow.py --init
python configs\source_root_workflow.py --update
```

The terminal is revealed before execution so users can see full output, respond
to any Git authentication prompt, and inspect failures. V1 does not parse logs,
modify lock files directly, or run `configs/source_roots.py` commands.

## Extension Layout

Place the extension source under `vscode-extension/` as an isolated Node package
inside this repository. Keep it independent from the Python package metadata in
`pyproject.toml`.

Expected MVP files:

- `vscode-extension/package.json`
- `vscode-extension/tsconfig.json`
- `vscode-extension/src/extension.ts`
- `vscode-extension/README.md`

The package contributes these commands:

- `freecm.init`
- `freecm.update`

The extension activates on workspace folders and command invocation. No settings
are required in V1.

## Acceptance Checks

- Opening a migrated downstream repository shows both status bar items.
- Clicking `Init` opens/reuses the `FreeCM` terminal and runs
  the platform Python command for `configs/source_root_workflow.py --init`.
- Clicking `Update` runs
  the platform Python command for `configs/source_root_workflow.py --update`
  in the same terminal.
- Opening FreeCM itself, or a repository without
  `configs/source_root_workflow.py`, does not show actionable status bar items.
- Multi-root workspaces choose the active editor folder when possible and
  otherwise ask the user to select an eligible folder.
