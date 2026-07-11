from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import freecm.workspace_lock as workspace_lock_module
from freecm.atomic_write import atomic_write_json, atomic_write_text
from freecm.workspace_lock import (
    WORKSPACE_LOCK_CONTRACT,
    WORKSPACE_LOCK_OWNER_FILE_NAME,
    workspace_lock_path,
    workspace_mutation_lock,
)


def atomic_sidecar_dir(path: Path) -> Path:
    return path.parent / ".freecm" / "atomic"


def assert_atomic_write_sidecars(testcase: unittest.TestCase, path: Path) -> None:
    sidecar_dir = atomic_sidecar_dir(path)
    testcase.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])
    testcase.assertFalse((path.parent / f".{path.name}.lock").exists())
    testcase.assertEqual(list(sidecar_dir.glob(f".{path.name}.*.tmp")), [])
    testcase.assertTrue((sidecar_dir / f".{path.name}.lock").is_file())


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_text_replaces_content_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "nested" / "source_roots.lock.jsonc"

            atomic_write_text(target, "first\n")
            atomic_write_text(target, "second\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "second\n")
            assert_atomic_write_sidecars(self, target)

    def test_atomic_write_json_keeps_existing_content_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "source_roots.lock.jsonc"
            target.write_text("original\n", encoding="utf-8")

            with mock.patch(
                "freecm.atomic_write.os.replace", side_effect=OSError("replace failed")
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    atomic_write_json(target, {"depsMode": "manual"})

            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
            assert_atomic_write_sidecars(self, target)

    def test_atomic_write_json_formats_with_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "source_roots.lock.jsonc"

            atomic_write_json(target, {"depsMode": "manual"})

            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"depsMode": "manual"},
            )
            self.assertTrue(target.read_text(encoding="utf-8").endswith("\n"))

    def test_atomic_write_text_serializes_concurrent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "source_roots.lock.jsonc"
            values = [
                json.dumps({"writer": index, "payload": "x" * 1024}, indent=2) + "\n"
                for index in range(16)
            ]

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda value: atomic_write_text(target, value), values))

            final_text = target.read_text(encoding="utf-8")
            self.assertIn(final_text, values)
            self.assertIsInstance(json.loads(final_text), dict)
            assert_atomic_write_sidecars(self, target)

    def test_workspace_mutation_lock_serializes_concurrent_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            order: list[str] = []

            def operation(label: str) -> None:
                with workspace_mutation_lock(repo_root):
                    self.assertTrue(workspace_lock_path(repo_root).is_dir())
                    order.append(f"{label}:start")
                    time.sleep(0.005)
                    order.append(f"{label}:end")

            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(operation, [f"op{index}" for index in range(8)]))

            self.assertFalse(workspace_lock_path(repo_root).exists())
            for index in range(0, len(order), 2):
                self.assertEqual(order[index].split(":")[0], order[index + 1].split(":")[0])

    def test_workspace_mutation_lock_writes_owner_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)

            with workspace_mutation_lock(repo_root):
                owner_data = json.loads(
                    (workspace_lock_path(repo_root) / WORKSPACE_LOCK_OWNER_FILE_NAME).read_text(
                        encoding="utf-8"
                    )
                )

            self.assertEqual(
                owner_data["schemaVersion"],
                WORKSPACE_LOCK_CONTRACT["schemaVersion"],
            )
            self.assertEqual(owner_data["pid"], os.getpid())
            self.assertEqual(owner_data["implementation"], "python")
            self.assertEqual(owner_data["hostname"], owner_data["hostname"].lower())
            self.assertEqual(len(owner_data["token"]), 32)

    def test_workspace_mutation_lock_retries_transient_windows_release_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)
            original_rename = Path.rename
            attempts = 0

            def flaky_rename(path: Path, target: Path) -> Path:
                nonlocal attempts
                if path == lock_path and attempts < 2:
                    attempts += 1
                    error = PermissionError(errno.EACCES, "sharing violation")
                    error.winerror = 32  # type: ignore[attr-defined]
                    raise error
                return original_rename(path, target)

            with (
                mock.patch.object(
                    workspace_lock_module,
                    "_is_transient_windows_rename_error",
                    return_value=True,
                ),
                mock.patch.object(Path, "rename", autospec=True, side_effect=flaky_rename),
            ):
                with workspace_mutation_lock(repo_root):
                    self.assertTrue(lock_path.is_dir())

            self.assertEqual(attempts, 2)
            self.assertFalse(lock_path.exists())
            self.assertNotIn(lock_path, workspace_lock_module._HELD_WORKSPACE_LOCKS)

    def test_workspace_mutation_lock_abandons_permanent_release_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)

            with (
                mock.patch.object(
                    workspace_lock_module,
                    "_is_transient_windows_rename_error",
                    return_value=True,
                ),
                mock.patch.object(
                    Path,
                    "rename",
                    autospec=True,
                    side_effect=PermissionError(errno.EACCES, "sharing violation"),
                ),
                self.assertRaisesRegex(RuntimeError, "Unable to retire workspace lock"),
            ):
                with workspace_mutation_lock(repo_root):
                    pass

            owner = workspace_lock_module._read_owner(lock_path)
            self.assertIsNotNone(owner)
            assert owner is not None
            self.assertTrue(workspace_lock_module._owner_is_abandoned(lock_path, owner))
            self.assertNotIn(lock_path, workspace_lock_module._HELD_WORKSPACE_LOCKS)

            with workspace_mutation_lock(
                repo_root,
                timeout_seconds=0.5,
                poll_seconds=0.005,
            ):
                self.assertTrue(lock_path.is_dir())
            self.assertFalse(lock_path.exists())

    def test_workspace_mutation_lock_clears_held_state_if_abandon_publish_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)

            with (
                mock.patch.object(
                    workspace_lock_module,
                    "_is_transient_windows_rename_error",
                    return_value=True,
                ),
                mock.patch.object(
                    Path,
                    "rename",
                    autospec=True,
                    side_effect=PermissionError(errno.EACCES, "sharing violation"),
                ),
                mock.patch.object(
                    workspace_lock_module,
                    "_mark_new_owner_abandoned",
                    return_value=False,
                ),
                self.assertRaisesRegex(RuntimeError, "Unable to retire workspace lock"),
            ):
                with workspace_mutation_lock(repo_root):
                    pass

            self.assertNotIn(lock_path, workspace_lock_module._HELD_WORKSPACE_LOCKS)

    def test_workspace_mutation_lock_is_reentrant_in_same_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)

            with workspace_mutation_lock(repo_root):
                self.assertTrue(workspace_lock_path(repo_root).is_dir())
                with workspace_mutation_lock(repo_root, timeout_seconds=0.001):
                    self.assertTrue(workspace_lock_path(repo_root).is_dir())
                self.assertTrue(workspace_lock_path(repo_root).is_dir())

            self.assertFalse(workspace_lock_path(repo_root).exists())

    def test_workspace_mutation_lock_times_out_when_existing_lock_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            workspace_lock_path(repo_root).mkdir()

            with self.assertRaisesRegex(
                TimeoutError,
                "current owner: missing or invalid owner metadata",
            ):
                with workspace_mutation_lock(repo_root, timeout_seconds=0.001):
                    pass

    def test_workspace_mutation_lock_recovers_invalid_metadata_after_grace(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)
            lock_path.mkdir()

            with self.assertRaises(TimeoutError):
                with workspace_mutation_lock(
                    repo_root,
                    timeout_seconds=0.001,
                    initialization_grace_seconds=1.0,
                ):
                    pass
            self.assertTrue(lock_path.is_dir())

            old_time = time.time() - 10.0
            os.utime(lock_path, (old_time, old_time))
            with workspace_mutation_lock(
                repo_root,
                timeout_seconds=0.2,
                initialization_grace_seconds=0.01,
            ):
                self.assertTrue((lock_path / WORKSPACE_LOCK_OWNER_FILE_NAME).is_file())
            self.assertFalse(lock_path.exists())

    def test_workspace_mutation_lock_recovers_orphan_reclaimer_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)
            lock_path.mkdir()
            old_time = time.time() - 10.0
            os.utime(lock_path, (old_time, old_time))
            probe = subprocess.Popen([sys.executable, "-c", "pass"])
            probe.wait(timeout=5.0)
            claim_data = {
                "schemaVersion": WORKSPACE_LOCK_CONTRACT["schemaVersion"],
                "token": "orphan-reclaimer",
                "pid": probe.pid,
                "processStartToken": None,
                "hostname": socket.gethostname().strip().lower(),
                "implementation": "python",
                "acquiredAt": "2026-01-01T00:00:00.000Z",
            }
            (lock_path / ".reclaim").write_text(
                json.dumps(claim_data) + "\n",
                encoding="utf-8",
            )

            with workspace_mutation_lock(
                repo_root,
                timeout_seconds=0.5,
                poll_seconds=0.005,
                initialization_grace_seconds=0.01,
            ):
                self.assertTrue(lock_path.is_dir())
            self.assertFalse(lock_path.exists())

    def test_workspace_mutation_lock_does_not_remove_active_reclaimer(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)
            original_write_owner = workspace_lock_module._write_owner

            def write_owner_with_active_claim(
                path: Path,
                owner: workspace_lock_module.WorkspaceLockOwner,
            ) -> None:
                original_write_owner(path, owner)
                workspace_lock_module._write_owner_path(
                    path / ".reclaim",
                    workspace_lock_module._new_owner(),
                )

            entered = False
            with (
                mock.patch.object(
                    workspace_lock_module,
                    "_write_owner",
                    side_effect=write_owner_with_active_claim,
                ),
                self.assertRaisesRegex(TimeoutError, "active reclaimer"),
            ):
                with workspace_mutation_lock(
                    repo_root,
                    timeout_seconds=0.02,
                    poll_seconds=0.005,
                ):
                    entered = True

            self.assertFalse(entered)
            self.assertTrue((lock_path / ".reclaim").is_file())
            owner = workspace_lock_module._read_owner(lock_path)
            self.assertIsNotNone(owner)
            assert owner is not None
            self.assertTrue(workspace_lock_module._owner_is_abandoned(lock_path, owner))
            (lock_path / ".reclaim").unlink()
            with workspace_mutation_lock(
                repo_root,
                timeout_seconds=0.5,
                poll_seconds=0.005,
            ):
                self.assertTrue(lock_path.is_dir())
            self.assertFalse(lock_path.exists())

    def test_abandon_marker_does_not_affect_replacement_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)
            retired_path = lock_path.with_name(f"{lock_path.name}.retired")
            lock_path.mkdir()
            old_owner = workspace_lock_module._new_owner()
            workspace_lock_module._write_owner(lock_path, old_owner)

            lock_path.rename(retired_path)
            lock_path.mkdir()
            replacement_owner = workspace_lock_module._new_owner()
            workspace_lock_module._write_owner(lock_path, replacement_owner)
            workspace_lock_module._mark_new_owner_abandoned(lock_path, old_owner)

            self.assertEqual(
                workspace_lock_module._read_owner(lock_path),
                replacement_owner,
            )
            self.assertFalse(
                workspace_lock_module._owner_is_abandoned(lock_path, replacement_owner)
            )
            shutil.rmtree(lock_path)
            shutil.rmtree(retired_path)

    def test_reclaim_claim_publication_has_no_partial_canonical_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            lock_path = workspace_lock_path(Path(tempdir))
            lock_path.mkdir()
            claim_path = lock_path / ".reclaim"
            first_link_started = threading.Event()
            release_first_link = threading.Event()
            original_link = os.link
            link_count = 0

            def delayed_first_link(source: Path, target: Path) -> None:
                nonlocal link_count
                link_count += 1
                if link_count == 1:
                    self.assertEqual(Path(target), claim_path)
                    self.assertIsNotNone(workspace_lock_module._read_owner_path(Path(source)))
                    self.assertFalse(claim_path.exists())
                    first_link_started.set()
                    self.assertTrue(release_first_link.wait(timeout=5.0))
                original_link(source, target)

            with (
                mock.patch.object(
                    workspace_lock_module.os,
                    "link",
                    side_effect=delayed_first_link,
                ),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                first_claim = executor.submit(
                    workspace_lock_module._acquire_reclaim_claim,
                    lock_path,
                )
                self.assertTrue(first_link_started.wait(timeout=1.0))
                time.sleep(
                    workspace_lock_module.WORKSPACE_LOCK_INITIALIZATION_GRACE_MS / 1000.0 + 0.01
                )
                self.assertFalse(claim_path.exists())
                second_claim = workspace_lock_module._acquire_reclaim_claim(lock_path)
                self.assertIsNotNone(second_claim)
                release_first_link.set()
                self.assertIsNone(first_claim.result(timeout=1.0))

            assert second_claim is not None
            self.assertEqual(
                workspace_lock_module._read_owner_path(claim_path),
                second_claim,
            )
            self.assertEqual(list(lock_path.glob(".reclaim.candidate.*")), [])

    def test_workspace_mutation_lock_write_failure_does_not_delete_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)
            retired_path = lock_path.with_name(f"{lock_path.name}.retired")
            original_write_owner = workspace_lock_module._write_owner

            def replace_then_fail(
                path: Path,
                _owner: workspace_lock_module.WorkspaceLockOwner,
            ) -> None:
                path.rename(retired_path)
                path.mkdir()
                original_write_owner(path, workspace_lock_module._new_owner())
                raise OSError("owner write failed")

            with (
                mock.patch.object(
                    workspace_lock_module,
                    "_write_owner",
                    side_effect=replace_then_fail,
                ),
                self.assertRaisesRegex(OSError, "owner write failed"),
            ):
                with workspace_mutation_lock(repo_root):
                    pass

            self.assertTrue((lock_path / WORKSPACE_LOCK_OWNER_FILE_NAME).is_file())
            shutil.rmtree(lock_path)
            shutil.rmtree(retired_path)

    def test_workspace_mutation_lock_reports_live_process_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            script = """
import sys
from pathlib import Path
from freecm.workspace_lock import workspace_mutation_lock
with workspace_mutation_lock(Path(sys.argv[1])):
    print("ready", flush=True)
    sys.stdin.readline()
"""
            child = subprocess.Popen(
                [sys.executable, "-c", script, str(repo_root)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.addCleanup(self._stop_process, child)
            assert child.stdout is not None
            self.assertEqual(child.stdout.readline().strip(), "ready")

            with self.assertRaisesRegex(
                TimeoutError,
                rf"current owner: pid={child.pid}.*implementation=python",
            ):
                with workspace_mutation_lock(repo_root, timeout_seconds=0.05):
                    pass
            self.assertTrue(workspace_lock_path(repo_root).is_dir())

            assert child.stdin is not None
            child.stdin.write("\n")
            child.stdin.flush()
            self.assertEqual(child.wait(timeout=5.0), 0)
            self.assertFalse(workspace_lock_path(repo_root).exists())

    def test_workspace_mutation_lock_throttles_live_owner_identity_probes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)
            lock_path.mkdir()
            owner = workspace_lock_module.WorkspaceLockOwner(
                token="live-owner",
                pid=os.getpid(),
                process_start_token="stable-token",
                hostname=socket.gethostname().strip().lower(),
                implementation="python",
                acquired_at="2026-01-01T00:00:00.000Z",
            )
            workspace_lock_module._write_owner(lock_path, owner)

            with (
                mock.patch.object(
                    workspace_lock_module,
                    "_process_identity",
                    return_value=("live", "stable-token"),
                ) as process_identity,
                self.assertRaises(TimeoutError),
            ):
                with workspace_mutation_lock(
                    repo_root,
                    timeout_seconds=0.3,
                    poll_seconds=0.005,
                ):
                    pass

            self.assertLessEqual(process_identity.call_count, 2)
            shutil.rmtree(lock_path)

    def test_workspace_mutation_lock_recovers_after_process_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            script = """
import os
import sys
from pathlib import Path
from freecm.workspace_lock import workspace_mutation_lock
with workspace_mutation_lock(Path(sys.argv[1])):
    print("ready", flush=True)
    os._exit(0)
"""
            child = subprocess.Popen(
                [sys.executable, "-c", script, str(repo_root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.addCleanup(self._stop_process, child)
            assert child.stdout is not None
            self.assertEqual(child.stdout.readline().strip(), "ready")
            self.assertEqual(child.wait(timeout=5.0), 0)
            self.assertTrue(workspace_lock_path(repo_root).is_dir())

            with workspace_mutation_lock(repo_root, timeout_seconds=0.5):
                self.assertTrue(workspace_lock_path(repo_root).is_dir())
            self.assertFalse(workspace_lock_path(repo_root).exists())

    def test_workspace_mutation_lock_does_not_release_changed_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)

            with self.assertRaisesRegex(RuntimeError, "ownership changed before release"):
                with workspace_mutation_lock(repo_root):
                    owner_path = lock_path / WORKSPACE_LOCK_OWNER_FILE_NAME
                    owner_data = json.loads(owner_path.read_text(encoding="utf-8"))
                    owner_data["token"] = "replacement-owner"
                    owner_path.write_text(json.dumps(owner_data) + "\n", encoding="utf-8")

            self.assertTrue(lock_path.is_dir())
            owner_path = lock_path / WORKSPACE_LOCK_OWNER_FILE_NAME
            owner_data = json.loads(owner_path.read_text(encoding="utf-8"))
            if owner_data["processStartToken"] is None:
                shutil.rmtree(lock_path)
                self.skipTest("process start identity is unavailable on this platform")
            owner_data["processStartToken"] = "reused-pid-token"
            owner_path.write_text(json.dumps(owner_data) + "\n", encoding="utf-8")
            with workspace_mutation_lock(repo_root, timeout_seconds=0.5):
                self.assertTrue(lock_path.is_dir())
            self.assertFalse(lock_path.exists())

    def test_workspace_mutation_lock_does_not_reclaim_other_host(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            lock_path = workspace_lock_path(repo_root)
            lock_path.mkdir()
            owner_data = {
                "schemaVersion": WORKSPACE_LOCK_CONTRACT["schemaVersion"],
                "token": "other-host-owner",
                "pid": 999999,
                "processStartToken": "other-host-token",
                "hostname": "other-host.invalid",
                "implementation": "python",
                "acquiredAt": "2026-01-01T00:00:00.000Z",
            }
            (lock_path / WORKSPACE_LOCK_OWNER_FILE_NAME).write_text(
                json.dumps(owner_data) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(TimeoutError, "hostname=other-host.invalid"):
                with workspace_mutation_lock(repo_root, timeout_seconds=0.01):
                    pass
            self.assertTrue(lock_path.is_dir())
            shutil.rmtree(lock_path)

    @staticmethod
    def _stop_process(child: subprocess.Popen[str]) -> None:
        try:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5.0)
        finally:
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()


if __name__ == "__main__":
    unittest.main()
