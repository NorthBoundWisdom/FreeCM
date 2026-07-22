# Git Hooks Usage Guide

This directory contains shared Git hooks for C++ repositories.

## Installation

1. Create a local config:

```bash
cd hooks
cp ./path.ini.sample ./path.ini
```

Windows PowerShell:

```powershell
Set-Location .\hooks
Copy-Item .\path.ini.sample .\path.ini
```

2. Fill real tool paths and repository-specific roots:

```ini
CLANG_FORMAT_PATH=<path to clang-format>
QMLFORMAT_PATH=<path to qmlformat>
SOURCE_ROOTS=SourceCode
EXCLUDE_DIRS=SourceCode/thirdparty
USE_GIT_CONFIG=true
```

3. Run the installer from `hooks/`:

```bash
python3 install.py
```

The installer uses Git's effective `core.hooksPath`, including relative custom
paths and linked worktrees. It never overwrites a different existing hook by
default. Choose an explicit policy when adopting a repository that already has
hooks:

```bash
# Keep each replaced file beside the installed hook as *.freecm-backup-*.
python3 install.py --existing backup

# Replace different existing files without retaining them after success.
python3 install.py --existing replace
```

All FreeCM hook files are staged before any destination changes. A staging or
publication failure restores the previous hook set; if restoration itself
fails, the installer reports and retains the recovery backup. Re-running the
installer is idempotent when the installed FreeCM files already match.

The installer stores local Git config keys under `freecm.*`.

## Features

- Formats staged C/C++ blobs under configured source roots with clang-format.
- Formats staged QML/JS blobs under configured source roots with qmlformat.
- Normalizes staged text blobs to LF and strips trailing whitespace.
- Uses the staged `.gitattributes` rules plus blob content for text/binary
  detection.
- Blocks staged blobs larger than 15MB, regardless of the worktree file size.
- Adds a commit message template and validates `[type]: description`.

The pre-commit hook reads content, modes, and paths from Git's index. It prepares
every formatter result before updating the index in one operation and never runs
`git add` on worktree files. When Git reports no unstaged change for a regular
worktree file, the hook writes the formatted result back so a fully staged
commit does not immediately leave formatting-only changes behind. The original
worktree bytes are rechecked before writing and retained for rollback, including
when Git clean filters make them differ from the staged blob.
Partially staged files keep their unstaged hunks, and index-only files are not
recreated. Formatter or worktree-update failures restore the original index and
any worktree files already updated. Executable modes are preserved; deletions,
symlinks, gitlinks, and unmerged entries are not rewritten.

## Requirements

- `python3`, `python`, or Windows `py -3`
- Valid `hooks/path.ini` with executable paths

## Supported Commit Types

- `[feat]`: new feature
- `[fix]`: bug fix
- `[refactor]`: code refactoring
- `[style]`: code style changes
- `[docs]`: documentation changes
- `[test]`: test case changes
- `[chore]`: other changes
- `[perf]`: performance optimization
- `[ci]`: continuous integration related
- `[build]`: build system changes
- `[enhancement]`: incremental improvement
