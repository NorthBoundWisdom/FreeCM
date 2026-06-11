from __future__ import annotations

import hashlib
import http.server
import json
import socketserver
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from freecm.asset_seeds import (
    asset_seed_file_names,
    asset_seed_root,
    build_parser,
    prepare_asset_seeds,
    require_asset_seeds,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args


class ThreadedHttpServer:
    def __init__(self, root: Path) -> None:
        def handler(*args: object, **kwargs: object) -> QuietHandler:
            return QuietHandler(*args, directory=str(root), **kwargs)

        self.server = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> ThreadedHttpServer:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        del args
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class AssetSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name) / "repo"
        self.root.mkdir()
        self.downloads = Path(self.tempdir.name) / "downloads"
        self.downloads.mkdir()

    def write_lock(self, assets: dict[str, object]) -> None:
        lock = {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "cmakeEnvironment": {},
            "cmakeCacheVariables": {},
            "depsManualPath": {},
            "dependencies": {},
            "assets": assets,
        }
        (self.root / "source_roots.lock.jsonc").write_text(
            json.dumps(lock, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_file_asset_downloads_and_verifies_hash(self) -> None:
        payload = b"asset-payload"
        (self.downloads / "asset.dat").write_bytes(payload)
        with ThreadedHttpServer(self.downloads) as server:
            self.write_lock(
                {
                    "AssetBundle": {
                        "seedPath": "build/dependency_seed_repos/AssetBundle",
                        "files": [
                            {
                                "id": "asset",
                                "type": "file",
                                "url": f"{server.url}/asset.dat",
                                "fileName": "asset.dat",
                                "sha256": sha256_bytes(payload),
                                "sizeBytes": len(payload),
                            }
                        ],
                    }
                }
            )

            prepared = prepare_asset_seeds(self.root)
            verified = require_asset_seeds(self.root)

        asset_path = self.root / "build" / "dependency_seed_repos" / "AssetBundle" / "asset.dat"
        self.assertEqual(b"asset-payload", asset_path.read_bytes())
        self.assertEqual("AssetBundle", prepared[0].asset_name)
        self.assertEqual("AssetBundle", verified[0].asset_name)
        self.assertTrue((asset_path.parent / "manifest.json").is_file())

    def test_archive_asset_extracts_declared_entries(self) -> None:
        dll = b"dll"
        license_text = b"license"
        archive_path = self.downloads / "vendor.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("vendor/bin/amd64/vendor.dll", dll)
            archive.writestr("vendor/LICENSE.txt", license_text)
            archive.writestr("ignored.txt", b"ignored")
        archive_bytes = archive_path.read_bytes()
        with ThreadedHttpServer(self.downloads) as server:
            self.write_lock(
                {
                    "vendor": {
                        "seedPath": "build/dependency_seed_repos/vendor",
                        "files": [
                            {
                                "id": "vendor",
                                "type": "archive",
                                "url": f"{server.url}/vendor.zip",
                                "fileName": "vendor.zip",
                                "sha256": sha256_bytes(archive_bytes),
                                "extract": [
                                    {
                                        "from": "vendor/bin/amd64/vendor.dll",
                                        "to": "Resources/Vendor/amd64/vendor.dll",
                                        "sha256": sha256_bytes(dll),
                                        "sizeBytes": len(dll),
                                    },
                                    {
                                        "from": "vendor/LICENSE.txt",
                                        "to": "Resources/Vendor/LICENSE.txt",
                                        "sha256": sha256_bytes(license_text),
                                    },
                                ],
                            }
                        ],
                    }
                }
            )

            prepare_asset_seeds(self.root)

        seed_root = self.root / "build" / "dependency_seed_repos" / "vendor"
        self.assertEqual(
            dll, (seed_root / "Resources" / "Vendor" / "amd64" / "vendor.dll").read_bytes()
        )
        self.assertEqual(
            license_text, (seed_root / "Resources" / "Vendor" / "LICENSE.txt").read_bytes()
        )
        self.assertFalse((seed_root / "ignored.txt").exists())

    def test_update_verify_fails_when_asset_is_missing(self) -> None:
        self.write_lock(
            {
                "AssetBundle": {
                    "seedPath": "build/dependency_seed_repos/AssetBundle",
                    "files": [
                        {
                            "id": "asset",
                            "type": "file",
                            "url": "https://example.invalid/asset.dat",
                            "fileName": "asset.dat",
                            "sha256": "a" * 64,
                        }
                    ],
                }
            }
        )

        with self.assertRaisesRegex(
            FileNotFoundError, "Run `python3 configs/source_root_workflow.py --init` first"
        ):
            require_asset_seeds(self.root)

    def test_unsafe_paths_and_legacy_fields_fail_fast(self) -> None:
        bad_lock = {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "depsManualPath": {},
            "dependencies": {},
            "assetDependencies": {},
        }
        (self.root / "source_roots.lock.jsonc").write_text(json.dumps(bad_lock), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "assetDependencies is no longer supported"):
            require_asset_seeds(self.root)

        self.write_lock(
            {
                "AssetBundle": {
                    "seedPath": "../outside",
                    "files": [
                        {
                            "id": "asset",
                            "type": "file",
                            "url": "https://example.invalid/asset.dat",
                            "fileName": "asset.dat",
                            "sha256": "a" * 64,
                        }
                    ],
                }
            }
        )
        with self.assertRaisesRegex(ValueError, "safe relative path"):
            require_asset_seeds(self.root)

    def test_asset_seed_helpers_read_seed_paths_and_names(self) -> None:
        self.write_lock(
            {
                "AssetBundle": {
                    "seedPath": "build/dependency_seed_repos/AssetBundle",
                    "files": [
                        {
                            "id": "asset",
                            "type": "file",
                            "url": "https://example.invalid/asset.dat",
                            "fileName": "asset.dat",
                            "sha256": "a" * 64,
                        }
                    ],
                }
            }
        )

        self.assertEqual(
            (self.root / "build" / "dependency_seed_repos" / "AssetBundle").resolve(),
            asset_seed_root(self.root, "AssetBundle"),
        )
        self.assertEqual(("asset.dat",), asset_seed_file_names(self.root, "AssetBundle"))

    def test_asset_cli_only_exposes_offline_verify(self) -> None:
        choices = build_parser()._subparsers._group_actions[0].choices

        self.assertIn("verify", choices)
        self.assertNotIn("prepare", choices)


if __name__ == "__main__":
    unittest.main()
