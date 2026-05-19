# FreeCM VS Code Extension

Run FreeCM source-root workflow commands from the VS Code status bar.

## MVP Behavior

The extension shows two status bar items when the current workspace folder has:

- `FreeCM/`
- `configs/source_root_workflow.py`
- `source_roots.lock.jsonc` or `source_roots.lock.jsonc.in`

The buttons run these commands in an integrated terminal named `FreeCM`.
Windows uses `python`; macOS and Linux use `python3`:

```bash
python3 configs/source_root_workflow.py --init
python3 configs/source_root_workflow.py --update
```

Repositories that only provide `scripts/source_root_workflow.py` are not
supported by V1.

The Workflow panel can also show repository-defined `Build`, `Test`, and `Run`
buttons when the workspace provides `configs/freecm.commands.jsonc`.
Commands are declared as argv arrays and are run from the repository root.
Use `command` + `args` for one terminal command, or `steps` for a small
ordered sequence such as configure, build, then test:

```jsonc
{
  "version": 1,
  "commands": {
    "build": [
      {
        "id": "mac-release",
        "label": "Mac Release",
        "steps": [
          {
            "command": "cmake",
            "args": ["--preset", "mac_clang_release"]
          },
          {
            "command": "cmake",
            "args": ["--build", "--preset", "mac_clang_release", "--target", "GeoToy"]
          }
        ],
        "platforms": ["darwin"],
        "default": true
      }
    ],
    "test": [],
    "run": []
  }
}
```

## Development

```bash
npm install
npm run compile
npm test
```

Use VS Code's extension host launch flow to try the extension against a migrated
downstream repository such as `Dwgatlas`.
