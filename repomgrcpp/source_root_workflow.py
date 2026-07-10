#!/usr/bin/env python3
# Usage:
#   python3 /path/to/FreeCM/repomgrcpp/source_root_workflow.py --init
#   python3 /path/to/FreeCM/repomgrcpp/source_root_workflow.py --update
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.source_root_workflow --help

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_REPO_ROOT))

from repomgrcpp.cmake_workflow import main

if __name__ == "__main__":
    raise SystemExit(main())
