#!/usr/bin/env python3
# Usage:
#   python3 /path/to/FreeCM/cpprepomgr/source_root_workflow.py --init
#   python3 /path/to/FreeCM/cpprepomgr/source_root_workflow.py --update
#   PYTHONPATH=/path/to/FreeCM python3 -m cpprepomgr.source_root_workflow --help

from __future__ import annotations

try:
    from .cmake_workflow import main
except ImportError:  # pragma: no cover - supports direct script execution.
    from cmake_workflow import main


if __name__ == "__main__":
    raise SystemExit(main())
