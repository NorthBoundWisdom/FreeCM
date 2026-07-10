# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.package.cli --help
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.package.cli validate-config --config <package.json> --platform mac|win|linux
#   package-tool --help

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from .common import PackageError, load_package_config
from .linux_deploy import deploy_linux
from .mac_deploy import deploy_mac
from .win_deploy import deploy_windows
from .wix import write_wix_fragment


def cmd_wix_fragment(args: argparse.Namespace) -> int:
    write_wix_fragment(
        Path(args.source),
        Path(args.output),
        root_id=args.root_id,
        prefix=args.prefix,
        component_group_id=args.component_group_id,
    )
    print(f"wrote {args.output}")
    return 0


def cmd_validate_config(args: argparse.Namespace) -> int:
    load_package_config(Path(args.config), platform=args.platform)
    print(f"valid {args.platform} package config: {args.config}")
    return 0


def cmd_deploy_win(args: argparse.Namespace) -> int:
    deploy_windows(load_package_config(Path(args.config), platform="win"))
    return 0


def cmd_deploy_mac(args: argparse.Namespace) -> int:
    deploy_mac(load_package_config(Path(args.config), platform="mac"))
    return 0


def cmd_deploy_linux(args: argparse.Namespace) -> int:
    deploy_linux(load_package_config(Path(args.config), platform="linux"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Config-driven C++ packaging tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    wix = subparsers.add_parser("wix-fragment", help="Generate WiX file component fragments.")
    wix.add_argument("--source", required=True)
    wix.add_argument("--output", required=True)
    wix.add_argument("--root-id", required=True)
    wix.add_argument("--prefix", required=True)
    wix.add_argument("--component-group-id")
    wix.set_defaults(func=cmd_wix_fragment)

    validate = subparsers.add_parser("validate-config", help="Validate a package JSON config.")
    validate.add_argument("--config", required=True)
    validate.add_argument("--platform", required=True, choices=("win", "mac", "linux"))
    validate.set_defaults(func=cmd_validate_config)

    win = subparsers.add_parser("deploy-win", help="Deploy a Windows package dist directory.")
    win.add_argument("--config", required=True)
    win.set_defaults(func=cmd_deploy_win)

    mac = subparsers.add_parser("deploy-mac", help="Deploy a macOS app bundle.")
    mac.add_argument("--config", required=True)
    mac.set_defaults(func=cmd_deploy_mac)

    linux = subparsers.add_parser("deploy-linux", help="Deploy a Linux AppDir.")
    linux.add_argument("--config", required=True)
    linux.set_defaults(func=cmd_deploy_linux)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        func: Callable[[argparse.Namespace], int] = args.func
        return func(args)
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
