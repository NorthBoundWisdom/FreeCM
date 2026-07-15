# FreeCM Agent Notes

This repository provides shared infrastructure for downstream repositories.
Keep changes small, adapter-oriented, and reusable across C++, Swift/Xcode,
Android, .NET, and mixed workspaces.

## Python Entry Documentation

- Every Python file that users may call directly must have a top-of-file
  `# Usage:` comment before imports.
- Treat these as user-callable:
  - files exposed by `pyproject.toml` console scripts;
  - files with a shebang or `if __name__ == "__main__"`;
  - `tools/*.py` and `repomgrcpp/tools/*.py` modules that back `repo_tool`
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
- Prefer adding C++/CMake-specific helpers under `repomgrcpp/tools/`.
- If a helper is useful to users, expose it through
  `repomgrcpp.tools.repo_tool` and add focused CLI tests.
- Keep library APIs importable as plain Python functions; the CLI should be a
  thin wrapper over those functions.

## Documentation Sources

- `README.md` is the user-facing product and workflow overview.
- `docs/dependency-lock-schema.md` owns lock, policy, and JSON report details.
- `docs/org-adoption-guide.md` owns organization rollout and governance notes.
- `docs/release-process.md` owns release steps.
- `hooks/README.md` owns commit hook behavior and valid commit types.
- `.codex/freecm-wiring/SKILL.md` owns downstream wiring SOPs for agents.
- Do not keep stale planning checklists in the repo. Fold durable rules into
  the relevant document above and delete completed or obsolete TODO files.

## No Downstream Defaults

- Do not hard-code downstream repository, product, asset, target, solution, or
  package names in FreeCM core, adapters, tests, or docs.
- Use neutral examples such as `LibA`, `LibB`, `SampleApp`, and `AssetBundle`
  when tests or documentation need concrete names.
- Host-specific dependency build order, CMake options, app targets, solution
  paths, and asset catalogs must be supplied by the downstream repository's
  explicit configuration or binding code.
- Do not keep transitional parsing paths for old command shapes. Prefer a clear
  validation error and require downstream repositories to update their wiring.

## Package Boundaries

- `freecm` is the cross-language dependency management core. Keep lock/schema,
  seed repository handling, materialization, asset seeds, path maps, terminal
  style, and generic source-root workflow scripting there.
- `repomgrcpp` is only for C++/CMake/package/repo-tool behavior. Non-C++
  repositories must not import it for generic dependency workflow APIs.
- `repomgrswift` is only for Swift/Xcode adapter behavior. It may depend on
  `freecm`, but must not depend on `repomgrcpp`.
- `repomgrandroid` is only for Android workflow helpers such as SDK/JDK
  environment setup, Gradle wrapper commands, layered Android test execution,
  and FreeCM command-validator discovery.
- `repomgrdotnet` is only for .NET/C# workflow helpers such as repo-local
  dotnet/NuGet environment isolation, solution build/test/run command helpers,
  and Windows exit-code normalization.
- Do not reintroduce removed package names, old aliases, or transitional adapter
  shims. Downstream repositories should rewire to `freecm` core plus the narrow
  adapter package they actually need.

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
- Keep version metadata synchronized whenever any FreeCM version changes:
  `VERSION`, `pyproject.toml`, `vscode-extension/package.json`, and
  `vscode-extension/package-lock.json` must all use the same exact version.
  Run `python3 scripts/check-version-consistency.py` after version edits.
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
  - Python type checks;
  - Python lint checks;
  - Python coverage reports;
  - Python security scans;
  - version consistency checks;
  - VS Code extension compile;
  - VS Code extension tests;
  - `npm audit --omit=optional`;
  - Python wheel and VSIX smoke tests when packaging changes require them;
  - `git diff --check`.
- Tags matching `v*` build VSIX artifacts on Linux, macOS, and Windows and
  publish them to GitHub Releases.
- Keep local validation commands aligned with the GitHub Actions workflow.

## Branch Policy

- Treat `master` as the only writable branch for this repository.
- Do not open pull requests for FreeCM work; commit verified changes directly to
  `master` and push `master`.
- Do not enable dependency-update automation that can only deliver changes as
  pull requests, including Dependabot or Renovate PR workflows. Dependency
  upgrades must be validated, committed, and pushed directly to `master`.
- Agents must not create feature branches, push agent-owned branches, or open
  pull requests for FreeCM work. Confirm the checkout is on `master`, make the
  change there, commit there, and push `master` directly.
- Do not create, push, or leave behind feature branches for FreeCM work.
- If a temporary branch is ever created locally, merge or fast-forward its changes into `master`, push `master`, and delete the temporary branch before finishing.

## Downstream Wiring Contract

- Downstream repositories should use a `FreeCM/` submodule and expose:
  - `configs/source_roots.py`;
  - `configs/source_root_workflow.py`;
  - `source_roots.lock.jsonc.in`.
- FreeCM must not impose a Git publication model on unrelated public hosts.
  Owner-managed downstream repositories that want latest tracking without PRs
  should explicitly adopt the policy template in
  `.codex/freecm-wiring/assets/owner-managed-latest.md` in their host-level
  agent instructions.
- Under that owner-managed policy, agents refresh `FreeCM/master` only from a
  clean host primary branch. An unchanged gitlink is a silent no-op: do not
  warn, commit, create a branch, or open a pull request. A changed gitlink must
  be validated, committed on the existing host primary branch, and pushed
  directly without a pull request.
- Routine latest tracking must use `git submodule update --remote --checkout
  FreeCM` from the host root. Do not run `git -C FreeCM pull` in a detached
  submodule or mistake a locally stale checkout for a new host change.
- The VS Code extension only targets `configs/source_root_workflow.py`; do not
  add fallback behavior for `scripts/source_root_workflow.py`.
- `source_roots.lock.jsonc.in` is the tracked template. The active
  `source_roots.lock.jsonc` is machine-local unless a host repository explicitly
  chooses otherwise.
- Python workflows and VS Code lock-mode controls share the downstream root
  `.freecm.workspace.lock` directory lock. Do not add adapter-specific
  workspace locks; release the lock before spawning another FreeCM process that
  must acquire the same lock.
- When diagnosing downstream source-root state, read the active
  `source_roots.lock.jsonc` first and prefer the host read-only commands:
  `python3 configs/source_roots.py status --format json` and
  `python3 configs/source_roots.py verify`.
- `--init` may use the network to prepare seed repositories. `--update` and the
  extension lock-mode controls must remain offline and operate from existing
  local seed repositories.
- `--init` is the only dependency workflow command allowed to clone repositories,
  download files, or prepare remote assets. The explicit VS Code `Pull Seeds`
  maintenance action may run `git pull --rebase` only in existing clean Git seed
  repositories; it must not create seeds, update locks, or materialize roots.
  `--update`, `materialize`, `verify`, `status`, VS Code lock-mode controls, repo
  command validation, and read-only diagnostics must never use the network.
- Tests for workflow changes must prove that non-`--init` paths keep
  `allow_network=False` and do not call clone/fetch/download helpers.
- When a downstream CMake configure fails against
  `build/dependency_source_roots/*`, first verify that the downstream repository
  ran its normal materialization command, usually
  `python3 configs/source_root_workflow.py --update`. A stale or half-materialized
  dependency root is not by itself evidence that FreeCM should change.
- If a downstream error mentions a dependency's nested `FreeCM/` submodule under
  `build/dependency_source_roots/*`, inspect that downstream materialized root
  and rerun its `--update` path before changing FreeCM. FreeCM changes should be
  reserved for failures reproducible after the standard `--init` / `--update`
  workflow from a clean downstream state.
- Do not treat `build/dependency_source_roots/*` as an editable source checkout;
  it is materialized output and may be replaced by the workflow. Dependency code
  edits should happen in an explicit manual checkout selected by `depsMode=manual`
  and `depsManualPath`, or in another developer-provided real checkout.
- Repositories materialized under `build/dependency_seed_repos/*` or
  `build/dependency_source_roots/*` must not enable their own dependency
  bootstrap/materialization flow while being consumed by a parent repository.
  The parent repository owns the dependency closure, install prefixes, and
  `CMAKE_PREFIX_PATH` for that build. Nested bootstrap from a seed or
  materialized dependency can silently build a second dependency graph and cause
  ABI mismatches.
- If a dependency is added with `add_subdirectory(...)` from a materialized
  source root, its CMake files must only consume already-prepared packages or
  targets from the parent build. Do not require that dependency's own `FreeCM/`
  submodule to be initialized in `build/dependency_source_roots/*`.
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
- Recommended project action order is `Config`, `Build`, `Run`, `Test`,
  `Package`. `Config` must remain explicit; `Build` should not silently run
  configuration first.
- macOS `cppkit_deploy_qt_dependencies` uses `macdeployqt` with
  `$<TARGET_BUNDLE_DIR:...>` and is intended for `.app` bundle targets. If a
  downstream repository has a plain helper executable, guard that downstream
  call by platform or make the helper a bundle; do not change FreeCM to mask an
  invalid target shape without a reusable cross-repository design.
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
python3 -m pip install -e ".[dev]"
python3 -m compileall -q freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
python3 scripts/check-version-consistency.py
python3 -m mypy
python3 -m ruff check freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
python3 -m black --check freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
python3 -m coverage run -m unittest discover -s tests -v
python3 -m coverage report
python3 -m bandit -q -r freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts
python3 -m pip_audit . --progress-spinner off
cd vscode-extension
npm test
npm audit --omit=optional
npm run package
cd ..
git diff --check
```
