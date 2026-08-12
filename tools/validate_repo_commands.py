# Usage:
#   python3 FreeCM/tools/validate_repo_commands.py /path/to/downstream

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

FREECM_ROOT = Path(__file__).resolve().parents[1]
if str(FREECM_ROOT) not in sys.path:
    sys.path.insert(0, str(FREECM_ROOT))

from freecm.errors import RepoCommandManifestError  # noqa: E402
from freecm.repo_commands import validate_repo_command_manifest  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate configs/freecm.commands.jsonc without Node or npm."
    )
    parser.add_argument("repo_root", nargs="?", default=".")
    args = parser.parse_args(argv)

    try:
        summary = validate_repo_command_manifest(Path(args.repo_root))
    except RepoCommandManifestError as error:
        print(error, file=sys.stderr)
        return 1

    print(
        f"Validated {summary.manifest_path} "
        f"({summary.configuration_count} Configs, {summary.variant_count} variants)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
