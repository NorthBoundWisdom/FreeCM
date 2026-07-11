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

Before committing or publishing a FreeCM change, run the canonical local
validation sequence in [the release process](docs/release-process.md). Keep
focused iteration fast with the affected tests, but complete the full sequence
before committing.

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

## Review

Keep one logical repository change per commit. Review should focus on
correctness, offline guarantees for non-init commands, package boundaries,
cross-platform behavior, and whether tests prove the affected behavior.

External contributors may open pull requests. Repository agents follow the
direct-to-`master` policy in `AGENTS.md` instead of opening agent-owned pull
requests.
