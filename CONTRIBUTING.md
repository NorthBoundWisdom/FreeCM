# Contributing to FreeCM

FreeCM changes should stay small, adapter-oriented, and reusable across C++,
Swift/Xcode, Android, .NET, and mixed workspaces.

## Local Setup

Use Python 3.10 or newer and Node.js 20 or newer.

```bash
python3 -m pip install -e .
cd vscode-extension
npm ci
cd ..
```

## Validation

Before opening a pull request, run:

```bash
python3 -m compileall -q freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
python3 -m unittest discover -s tests -v
python3 scripts/check-version-consistency.py
cd vscode-extension
npm test
npm audit --omit=optional
cd ..
git diff --check
```

For packaging changes, also run:

```bash
python3 -m pip install build
python3 -m build
cd vscode-extension
npm run package
```

## Lock Schema Changes

`source_roots.lock.jsonc.in` uses JSONC and `schemaVersion: 5`. Schema changes
must include focused regression tests, README or `docs/dependency-lock-schema.md`
updates, and either a migration path or a clear validation error for unsupported
old fields.

## Regression Tests

Add tests for bug fixes before or with the fix. High-value cases include
dependency aliases where `dependencyName` differs from `repoName`, nested
dependency closure, offline materialization, unsafe paths, shell quoting, and
command-manifest validation.

## Pull Request Review

Keep one logical repository change per PR. Review should focus on correctness,
offline guarantees for non-init commands, package boundaries, cross-platform
behavior, and whether tests prove the affected behavior.
