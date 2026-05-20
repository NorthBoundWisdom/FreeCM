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


from depsfixture.asset_seeds import (
    asset_seed_file_names,
    asset_seed_root,
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
        handler = lambda *args, **kwargs: QuietHandler(*args, directory=str(root), **kwargs)
        self.server = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "ThreadedHttpServer":
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
        payload = b"geo-data"
        (self.downloads / "geoip.dat").write_bytes(payload)
        with ThreadedHttpServer(self.downloads) as server:
            self.write_lock(
                {
                    "GeoData": {
                        "seedPath": "build/dependency_seed_repos/GeoData",
                        "files": [
                            {
                                "id": "geoip",
                                "type": "file",
                                "url": f"{server.url}/geoip.dat",
                                "fileName": "geoip.dat",
                                "sha256": sha256_bytes(payload),
                                "sizeBytes": len(payload),
                            }
                        ],
                    }
                }
            )

            prepared = prepare_asset_seeds(self.root)
            verified = require_asset_seeds(self.root)

        asset_path = self.root / "build" / "dependency_seed_repos" / "GeoData" / "geoip.dat"
        self.assertEqual(b"geo-data", asset_path.read_bytes())
        self.assertEqual("GeoData", prepared[0].asset_name)
        self.assertEqual("GeoData", verified[0].asset_name)
        self.assertTrue((asset_path.parent / "manifest.json").is_file())

    def test_archive_asset_extracts_declared_entries(self) -> None:
        dll = b"dll"
        license_text = b"license"
        archive_path = self.downloads / "wintun.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("wintun/bin/amd64/wintun.dll", dll)
            archive.writestr("wintun/LICENSE.txt", license_text)
            archive.writestr("ignored.txt", b"ignored")
        archive_bytes = archive_path.read_bytes()
        with ThreadedHttpServer(self.downloads) as server:
            self.write_lock(
                {
                    "wintun": {
                        "seedPath": "build/dependency_seed_repos/wintun",
                        "files": [
                            {
                                "id": "wintun",
                                "type": "archive",
                                "url": f"{server.url}/wintun.zip",
                                "fileName": "wintun.zip",
                                "sha256": sha256_bytes(archive_bytes),
                                "extract": [
                                    {
                                        "from": "wintun/bin/amd64/wintun.dll",
                                        "to": "Resources/Wintun/amd64/wintun.dll",
                                        "sha256": sha256_bytes(dll),
                                        "sizeBytes": len(dll),
                                    },
                                    {
                                        "from": "wintun/LICENSE.txt",
                                        "to": "Resources/Wintun/LICENSE.txt",
                                        "sha256": sha256_bytes(license_text),
                                    },
                                ],
                            }
                        ],
                    }
                }
            )

            prepare_asset_seeds(self.root)

        seed_root = self.root / "build" / "dependency_seed_repos" / "wintun"
        self.assertEqual(dll, (seed_root / "Resources" / "Wintun" / "amd64" / "wintun.dll").read_bytes())
        self.assertEqual(license_text, (seed_root / "Resources" / "Wintun" / "LICENSE.txt").read_bytes())
        self.assertFalse((seed_root / "ignored.txt").exists())

    def test_update_verify_fails_when_asset_is_missing(self) -> None:
        self.write_lock(
            {
                "GeoData": {
                    "seedPath": "build/dependency_seed_repos/GeoData",
                    "files": [
                        {
                            "id": "geoip",
                            "type": "file",
                            "url": "https://example.invalid/geoip.dat",
                            "fileName": "geoip.dat",
                            "sha256": "a" * 64,
                        }
                    ],
                }
            }
        )

        with self.assertRaisesRegex(FileNotFoundError, "Run `python3 configs/source_root_workflow.py --init` first"):
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
                "GeoData": {
                    "seedPath": "../outside",
                    "files": [
                        {
                            "id": "geoip",
                            "type": "file",
                            "url": "https://example.invalid/geoip.dat",
                            "fileName": "geoip.dat",
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
                "GeoData": {
                    "seedPath": "build/dependency_seed_repos/GeoData",
                    "files": [
                        {
                            "id": "geoip",
                            "type": "file",
                            "url": "https://example.invalid/geoip.dat",
                            "fileName": "geoip.dat",
                            "sha256": "a" * 64,
                        }
                    ],
                }
            }
        )

        self.assertEqual(
            (self.root / "build" / "dependency_seed_repos" / "GeoData").resolve(),
            asset_seed_root(self.root, "GeoData"),
        )
        self.assertEqual(("geoip.dat",), asset_seed_file_names(self.root, "GeoData"))


if __name__ == "__main__":
    unittest.main()
