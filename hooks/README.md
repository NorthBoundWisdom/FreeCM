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

The installer stores local Git config keys under `repoconfigsmgr.*`.

## Features

- Formats staged C/C++ files under configured source roots with clang-format.
- Formats staged QML/JS files under configured source roots with qmlformat.
- Normalizes staged text files to LF and strips trailing whitespace.
- Blocks files larger than 15MB.
- Adds a commit message template and validates `[type]: description`.

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
