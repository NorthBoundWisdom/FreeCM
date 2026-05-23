# FreeCM Agent Notes

This repository provides shared infrastructure for downstream repositories.
Keep changes small, adapter-oriented, and reusable across C++, Swift/Xcode, and
mixed workspaces.

## Python Entry Documentation

- Every Python file that users may call directly must have a top-of-file
  `# Usage:` comment before imports.
- Treat these as user-callable:
  - files exposed by `pyproject.toml` console scripts;
  - files with a shebang or `if __name__ == "__main__"`;
  - `tools/*.py` and `cpprepomgr/tools/*.py` modules that back `repo_tool`
    user commands.
- `hooks/install.py` is a user-facing installer and must have `# Usage:`.
- Hook implementation helpers under `hooks/`, such as `format.py` and
  `pre_commit.py`, are internal hook implementation details. Do not document
  them as user CLIs; mark them with a top-of-file `# Internal:` comment instead.
- Keep `tests/test_repo_tools.py` coverage in sync so missing `Usage` or
  `Internal` headers are caught by tests.

## Shared Tooling

- Prefer adding reusable maintenance helpers under `tools/` for generic
  cross-language behavior.
- Prefer adding C++/CMake-specific helpers under `cpprepomgr/tools/`.
- If a helper is useful to users, expose it through
  `cpprepomgr.tools.repo_tool` and add focused CLI tests.
- Keep library APIs importable as plain Python functions; the CLI should be a
  thin wrapper over those functions.

## Build Cleanup

- Stale build cleanup must preserve FreeCM dependency roots by default:
  `build/dependency_seed_repos` and `build/dependency_source_roots`.
- Destructive cleanup of generated project files such as `*.xcodeproj` must be
  explicit rather than default.
- Always support `--dry-run` for cleanup commands that delete files.

## VS Code Extension Release

- The VS Code extension lives under `vscode-extension/`.
- Any change to extension code, manifest, resources, tests, packaging scripts,
  or bundled VSIX artifacts must bump `vscode-extension/package.json` `version`
  in the same change.
- Keep `vscode-extension/src/buildInfo.ts` generated-only. It is created by
  `npm run compile` and must not be edited by hand.
- After bumping the version, run the extension validation and packaging flow:

  ```bash
  cd vscode-extension
  npm test
  npm audit --omit=optional
  npm run package
  ```

- `npm run package` is the release step. A release is considered published for
  this repository once the VSIX is compiled into the repo-root `plugin/`
  directory.
- The VSIX filename must be:

  ```text
  FreeCM_<platform>_v<version>.vsix
  ```

  where `<platform>` is `process.platform-process.arch` from Node.js and
  `<version>` is the exact `vscode-extension/package.json` version.
- Same-version/same-platform packages are overwritten by `npm run package`;
  different versions or platforms remain side by side in `plugin/`.
- Do not hand-name VSIX files or place release artifacts outside `plugin/`.
- For the public GitHub repository, prefer GitHub Release artifacts over tracked
  VSIX files. If the repository stops tracking `plugin/*.vsix`, keep local
  packaging output in `plugin/` but do not re-add generated VSIX files unless the
  user explicitly asks.

## GitHub Actions

- The CI workflow lives at `.github/workflows/ci.yml`.
- Pushes and pull requests must run:
  - Python compileall;
  - Python unittest discovery;
  - VS Code extension compile;
  - VS Code extension tests;
  - `npm audit --omit=optional`;
  - `git diff --check`.
- Tags matching `v*` build VSIX artifacts on Linux, macOS, and Windows and
  publish them to GitHub Releases.
- Keep local validation commands aligned with the GitHub Actions workflow.

## Branch Policy

- Treat `master` as the only writable branch for this repository.
- Do not create, push, or leave behind feature branches for FreeCM work.
- If a temporary branch is ever created locally, merge or fast-forward its changes into `master`, push `master`, and delete the temporary branch before finishing.

## Downstream Wiring Contract

- Downstream repositories should use a `FreeCM/` submodule and expose:
  - `configs/source_roots.py`;
  - `configs/source_root_workflow.py`;
  - `source_roots.lock.jsonc.in`.
- The VS Code extension only targets `configs/source_root_workflow.py`; do not
  add fallback behavior for legacy `scripts/source_root_workflow.py`.
- `source_roots.lock.jsonc.in` is the tracked template. The active
  `source_roots.lock.jsonc` is machine-local unless a host repository explicitly
  chooses otherwise.
- When diagnosing downstream source-root state, read the active
  `source_roots.lock.jsonc` first and prefer the host read-only commands:
  `python3 configs/source_roots.py status --format json` and
  `python3 configs/source_roots.py verify`.
- `--init` may use the network to prepare seed repositories. `--update` and the
  extension lock-mode controls must remain offline and operate from existing
  local seed repositories.
- Do not treat `build/dependency_source_roots/*` as an editable source checkout;
  it is materialized output and may be replaced by the workflow. Dependency code
  edits should happen in an explicit manual checkout selected by `depsMode=manual`
  and `depsManualPath`, or in another developer-provided real checkout.
- If downstream code starts depending on a changed dependency ABI, enum, struct,
  or behavior, do not leave the committed template pointing at the old
  dependency commit. Push the dependency commit first, confirm it exists on the
  remote with `git ls-remote <remote> <sha>`, then update the parent
  `source_roots.lock.jsonc.in`.
- For multi-repository changes, update and publish lock templates in dependency
  topology order: lower-level libraries first, then intermediate dependencies,
  then final app or product repositories. Do not only update the top-level lock.
- Project commands belong in `configs/freecm.commands.jsonc`; keep them
  explicit `command` + `args` or `steps` arrays rather than shell strings.
- Recommended project action order is `Config`, `Build`, `Run`, `Test`. `Config`
  must remain explicit; `Build` should not silently run configuration first.
- Downstream repositories should validate project commands before committing:

  ```bash
  node FreeCM/vscode-extension/out/validateRepoCommands.js --preview .
  ```

  The validator uses the same parser and terminal quoting as the extension.
- `Run` commands should stay attached to the FreeCM terminal. On macOS, avoid
  `open path/to/App.app` for normal run variants; prefer the executable under
  `.app/Contents/MacOS/` so logs stream in the terminal and `Ctrl+C` can stop
  the process.

## Commit Discipline

- Use the shared hook message format: `[type]: description`.
- Valid commit types are documented in `hooks/README.md` and enforced by
  `hooks/commit_msg.py`.
- Keep one logical repository change per commit when coordinating source-root
  dependency updates; dependency commits should be pushed before parent lock
  templates point at them.

## Validation

Before committing FreeCM changes, run:

```bash
python3 -m compileall -q freecm cpprepomgr swiftrepomgr tools hooks tests
python3 -m unittest discover -s tests -v
cd vscode-extension
npm test
npm audit --omit=optional
cd ..
git diff --check
```
