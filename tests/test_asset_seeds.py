from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import socketserver
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from freecm.asset_seeds import (
    asset_seed_file_names,
    asset_seed_root,
    build_parser,
    prepare_asset_seeds,
    require_asset_seeds,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TrackingBytesIO(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = super().read(size)
        self.bytes_read += len(data)
        return data


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

    def seed_root(self, asset_name: str = "AssetBundle") -> Path:
        return self.root / "build" / "dependency_seed_repos" / asset_name

    def write_archive_lock(
        self,
        archive_path: Path,
        *,
        extracted: list[dict[str, object]],
        limits: dict[str, object] | None = None,
        asset_name: str = "AssetBundle",
    ) -> None:
        archive_bytes = archive_path.read_bytes()
        asset: dict[str, object] = {
            "seedPath": f"build/dependency_seed_repos/{asset_name}",
            "files": [
                {
                    "id": "archive",
                    "type": "archive",
                    "url": archive_path.as_uri(),
                    "fileName": archive_path.name,
                    "sha256": sha256_bytes(archive_bytes),
                    "sizeBytes": len(archive_bytes),
                    "extract": extracted,
                }
            ],
        }
        if limits is not None:
            asset["limits"] = limits
        self.write_lock({asset_name: asset})

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
                                "sizeBytes": len(archive_bytes),
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
                                        "sizeBytes": len(license_text),
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

    def test_asset_entries_require_exact_size_bytes(self) -> None:
        cases = (
            {
                "type": "file",
                "url": "https://example.invalid/asset.dat",
                "fileName": "asset.dat",
                "sha256": "a" * 64,
            },
            {
                "type": "archive",
                "url": "https://example.invalid/archive.zip",
                "fileName": "archive.zip",
                "sha256": "a" * 64,
                "extract": [
                    {
                        "from": "asset.dat",
                        "to": "asset.dat",
                        "sha256": "b" * 64,
                        "sizeBytes": 1,
                    }
                ],
            },
            {
                "type": "archive",
                "url": "https://example.invalid/archive.zip",
                "fileName": "archive.zip",
                "sha256": "a" * 64,
                "sizeBytes": 1,
                "extract": [
                    {
                        "from": "asset.dat",
                        "to": "asset.dat",
                        "sha256": "b" * 64,
                    }
                ],
            },
        )
        for item in cases:
            with self.subTest(item_type=item["type"]):
                self.write_lock(
                    {
                        "AssetBundle": {
                            "seedPath": "build/dependency_seed_repos/AssetBundle",
                            "files": [item],
                        }
                    }
                )
                with self.assertRaisesRegex(ValueError, "sizeBytes"):
                    require_asset_seeds(self.root)

    def test_streaming_download_limit_preserves_destination_and_cleans_temp(self) -> None:
        payload = b"x" * 32
        self.write_lock(
            {
                "AssetBundle": {
                    "seedPath": "build/dependency_seed_repos/AssetBundle",
                    "limits": {"maxDownloadBytes": 16},
                    "files": [
                        {
                            "type": "file",
                            "url": "https://example.invalid/stream.bin",
                            "fileName": "stream.bin",
                            "sha256": sha256_bytes(b"x" * 8),
                            "sizeBytes": 8,
                        }
                    ],
                }
            }
        )
        seed_root = self.seed_root()
        seed_root.mkdir(parents=True)
        destination = seed_root / "stream.bin"
        destination.write_bytes(b"old")
        response = TrackingBytesIO(payload)

        with (
            mock.patch(
                "freecm.asset_seeds.urllib.request.urlopen",
                return_value=response,
            ),
            self.assertRaisesRegex(RuntimeError, "while streaming"),
        ):
            prepare_asset_seeds(self.root)

        self.assertEqual(response.bytes_read, 9)
        self.assertEqual(destination.read_bytes(), b"old")
        self.assertEqual(sorted(path.name for path in seed_root.iterdir()), ["stream.bin"])

    def test_download_hash_mismatch_preserves_destination_and_cleans_temp(self) -> None:
        payload = b"download"
        source = self.downloads / "download.bin"
        source.write_bytes(payload)
        self.write_lock(
            {
                "AssetBundle": {
                    "seedPath": "build/dependency_seed_repos/AssetBundle",
                    "files": [
                        {
                            "type": "file",
                            "url": source.as_uri(),
                            "fileName": "download.bin",
                            "sha256": "a" * 64,
                            "sizeBytes": len(payload),
                        }
                    ],
                }
            }
        )
        seed_root = self.seed_root()
        seed_root.mkdir(parents=True)
        destination = seed_root / "download.bin"
        destination.write_bytes(b"old")

        with self.assertRaisesRegex(RuntimeError, "did not match lock"):
            prepare_asset_seeds(self.root)

        self.assertEqual(destination.read_bytes(), b"old")
        self.assertEqual(sorted(path.name for path in seed_root.iterdir()), ["download.bin"])

    def test_file_publish_failure_restores_existing_destination(self) -> None:
        payload = b"download"
        source = self.downloads / "download.bin"
        source.write_bytes(payload)
        self.write_lock(
            {
                "AssetBundle": {
                    "seedPath": "build/dependency_seed_repos/AssetBundle",
                    "files": [
                        {
                            "type": "file",
                            "url": source.as_uri(),
                            "fileName": "download.bin",
                            "sha256": sha256_bytes(payload),
                            "sizeBytes": len(payload),
                        }
                    ],
                }
            }
        )
        seed_root = self.seed_root()
        seed_root.mkdir(parents=True)
        destination = seed_root / "download.bin"
        destination.write_bytes(b"old")

        with (
            mock.patch(
                "freecm.asset_seeds._normalize_permissions",
                side_effect=OSError("chmod failed"),
            ),
            self.assertRaisesRegex(OSError, "chmod failed"),
        ):
            prepare_asset_seeds(self.root)

        self.assertEqual(destination.read_bytes(), b"old")
        self.assertEqual(sorted(path.name for path in seed_root.iterdir()), ["download.bin"])

    def test_archive_limits_reject_member_count_size_total_and_ratio_bombs(self) -> None:
        cases = (
            (
                "members",
                [("selected.bin", b"a"), ("ignored.bin", b"b")],
                zipfile.ZIP_STORED,
                {"maxArchiveMembers": 1},
                "members",
            ),
            (
                "member-size",
                [("selected.bin", b"a"), ("ignored.bin", b"12345")],
                zipfile.ZIP_STORED,
                {"maxArchiveMemberBytes": 4, "maxArchiveTotalBytes": 16},
                "expands to",
            ),
            (
                "total-size",
                [("selected.bin", b"aa"), ("ignored.bin", b"bbbb")],
                zipfile.ZIP_STORED,
                {"maxArchiveMemberBytes": 5, "maxArchiveTotalBytes": 5},
                "total bytes",
            ),
            (
                "ratio",
                [("selected.bin", b"a"), ("ignored.bin", b"z" * 1024)],
                zipfile.ZIP_DEFLATED,
                {"maxCompressionRatio": 2},
                "compression ratio",
            ),
            (
                "duplicate",
                [
                    ("selected.bin", b"a"),
                    ("ignored//file.bin", b"b"),
                    ("ignored/file.bin", b"c"),
                ],
                zipfile.ZIP_STORED,
                {},
                "duplicate member path",
            ),
        )
        for name, members, compression, limits, message in cases:
            with self.subTest(name=name):
                asset_name = f"Asset-{name}"
                archive_path = self.downloads / f"{name}.zip"
                with zipfile.ZipFile(archive_path, "w", compression=compression) as archive:
                    for member_name, content in members:
                        archive.writestr(member_name, content)
                selected = members[0][1]
                self.write_archive_lock(
                    archive_path,
                    extracted=[
                        {
                            "from": "selected.bin",
                            "to": "selected.bin",
                            "sha256": sha256_bytes(selected),
                            "sizeBytes": len(selected),
                        }
                    ],
                    limits=limits,
                    asset_name=asset_name,
                )

                with self.assertRaisesRegex(RuntimeError, message):
                    prepare_asset_seeds(self.root)

                seed_root = self.seed_root(asset_name)
                self.assertFalse((seed_root / "selected.bin").exists())
                self.assertEqual(
                    sorted(path.name for path in seed_root.iterdir()),
                    [archive_path.name],
                )

    def test_archive_hash_failure_publishes_no_partial_outputs(self) -> None:
        first = b"first"
        second = b"second"
        archive_path = self.downloads / "partial.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("first.bin", first)
            archive.writestr("second.bin", second)
        self.write_archive_lock(
            archive_path,
            extracted=[
                {
                    "from": "first.bin",
                    "to": "out/first.bin",
                    "sha256": sha256_bytes(first),
                    "sizeBytes": len(first),
                },
                {
                    "from": "second.bin",
                    "to": "out/second.bin",
                    "sha256": "a" * 64,
                    "sizeBytes": len(second),
                },
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "did not match lock"):
            prepare_asset_seeds(self.root)

        seed_root = self.seed_root()
        self.assertFalse((seed_root / "out/first.bin").exists())
        self.assertFalse((seed_root / "out/second.bin").exists())
        self.assertEqual(list((seed_root / "out").iterdir()), [])

    def test_archive_publish_failure_restores_existing_outputs(self) -> None:
        first = b"new-first"
        second = b"new-second"
        archive_path = self.downloads / "rollback.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("first.bin", first)
            archive.writestr("second.bin", second)
        self.write_archive_lock(
            archive_path,
            extracted=[
                {
                    "from": "first.bin",
                    "to": "out/first.bin",
                    "sha256": sha256_bytes(first),
                    "sizeBytes": len(first),
                },
                {
                    "from": "second.bin",
                    "to": "out/second.bin",
                    "sha256": sha256_bytes(second),
                    "sizeBytes": len(second),
                },
            ],
        )
        output_root = self.seed_root() / "out"
        output_root.mkdir(parents=True)
        first_destination = output_root / "first.bin"
        second_destination = output_root / "second.bin"
        first_destination.write_bytes(b"old-first")
        second_destination.write_bytes(b"old-second")

        with (
            mock.patch(
                "freecm.asset_seeds._normalize_permissions",
                side_effect=[None, None, OSError("chmod failed")],
            ),
            self.assertRaisesRegex(OSError, "chmod failed"),
        ):
            prepare_asset_seeds(self.root)

        self.assertEqual(first_destination.read_bytes(), b"old-first")
        self.assertEqual(second_destination.read_bytes(), b"old-second")
        self.assertEqual(
            sorted(path.name for path in output_root.iterdir()),
            ["first.bin", "second.bin"],
        )

    def test_archive_rollback_failure_retains_recovery_backup(self) -> None:
        first = b"new-first"
        second = b"new-second"
        archive_path = self.downloads / "rollback-retain.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("first.bin", first)
            archive.writestr("second.bin", second)
        self.write_archive_lock(
            archive_path,
            extracted=[
                {
                    "from": "first.bin",
                    "to": "out/first.bin",
                    "sha256": sha256_bytes(first),
                    "sizeBytes": len(first),
                },
                {
                    "from": "second.bin",
                    "to": "out/second.bin",
                    "sha256": sha256_bytes(second),
                    "sizeBytes": len(second),
                },
            ],
        )
        output_root = self.seed_root() / "out"
        output_root.mkdir(parents=True)
        first_destination = output_root / "first.bin"
        second_destination = output_root / "second.bin"
        first_destination.write_bytes(b"old-first")
        second_destination.write_bytes(b"old-second")
        original_replace = os.replace

        def fail_first_restore(source: str | Path, target: str | Path) -> None:
            source_path = Path(source)
            target_path = Path(target)
            if (
                target_path.resolve() == first_destination.resolve()
                and source_path.exists()
                and source_path.read_bytes() == b"old-first"
            ):
                raise OSError("restore failed")
            original_replace(source_path, target_path)

        with (
            mock.patch("os.replace", side_effect=fail_first_restore),
            mock.patch(
                "freecm.asset_seeds._normalize_permissions",
                side_effect=[None, None, OSError("chmod failed")],
            ),
            self.assertRaisesRegex(RuntimeError, "retained backups"),
        ):
            prepare_asset_seeds(self.root)

        self.assertFalse(first_destination.exists())
        self.assertEqual(second_destination.read_bytes(), b"old-second")
        retained = [
            path
            for path in output_root.iterdir()
            if path.is_file() and path.read_bytes() == b"old-first"
        ]
        self.assertEqual(len(retained), 1)

    def test_asset_limits_reject_invalid_and_declared_oversized_values(self) -> None:
        payload = b"asset"
        source = self.downloads / "asset.bin"
        source.write_bytes(payload)
        invalid_limits = (
            ({"maxDownloadBytes": 0}, "positive integer"),
            ({"maxCompressionRatio": 0.5}, ">= 1"),
            (
                {"maxArchiveMemberBytes": 10, "maxArchiveTotalBytes": 5},
                "must be at least",
            ),
            ({"unknown": 1}, "unexpected fields"),
            ({"maxDownloadBytes": len(payload) - 1}, "exceeds maxDownloadBytes"),
        )
        for limits, message in invalid_limits:
            with self.subTest(limits=limits):
                self.write_lock(
                    {
                        "AssetBundle": {
                            "seedPath": "build/dependency_seed_repos/AssetBundle",
                            "limits": limits,
                            "files": [
                                {
                                    "type": "file",
                                    "url": source.as_uri(),
                                    "fileName": "asset.bin",
                                    "sha256": sha256_bytes(payload),
                                    "sizeBytes": len(payload),
                                }
                            ],
                        }
                    }
                )
                with self.assertRaisesRegex(ValueError, message):
                    require_asset_seeds(self.root)

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
                            "sizeBytes": 1,
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
                            "sizeBytes": 1,
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
                            "sizeBytes": 1,
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
