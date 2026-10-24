# Usage:
#   python3 FreeCM/tools/configure_public_seeds.py --repo-root . --seeds-root /path/to/seeds
#   repo-tool configure-public-seeds --repo-root . --seeds-root /path/to/seeds

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from freecm.public_seed_config import configure_public_seeds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select existing public seeds offline.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--seeds-root", required=True)
    args = parser.parse_args(argv)
    try:
        selected = configure_public_seeds(Path(args.repo_root), Path(args.seeds_root))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for name, seed in selected.items():
        print(f"{name}: public seed -> {seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
