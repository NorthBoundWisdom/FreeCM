from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from freecm.dependency_roots import DependencyRootConfig
from freecm.dependency_workflow import DependencyRootWorkflowFacade
from freecm.source_root_workflow import SourceRootWorkflowScript
from repomgrcpp.cmake_workflow_binding import bind_cmake_workflow_script


class WorkflowInitializationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.template = self.root / "source_roots.lock.jsonc.in"
        self.template.write_text(
            json.dumps(
                {
                    "schemaVersion": 5,
                    "depsMode": "pinned",
                    "depsManualPath": {},
                    "dependencies": {},
                }
            )
        )
        self.facade = DependencyRootWorkflowFacade(DependencyRootConfig(self.root, (), "Host"))
        self.generic = SourceRootWorkflowScript(self.facade, repo_display_name="Host")
        self.cmake = bind_cmake_workflow_script(
            {"workflow": self.facade._manager},
            repo_root=self.root,
            repo_display_name="Host",
            dependency_build_order=(),
        )
        self.active = self.root / "source_roots.lock.jsonc"

    def run_script(self, kind: str, flag: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if kind == "generic":
                return self.generic.main([flag])
            with mock.patch.object(sys, "argv", ["workflow", flag]):
                return self.cmake.main()

    def test_initrepo_requires_explicit_lock_creation(self) -> None:
        for kind in ("generic", "cmake"):
            with self.subTest(kind=kind):
                self.assertEqual(self.run_script(kind, "--initrepo"), 1)
                self.assertFalse(self.active.exists())
                self.assertFalse((self.root / "build").exists())

    def test_removed_init_flag_is_rejected(self) -> None:
        for kind in ("generic", "cmake"):
            with self.subTest(kind=kind), self.assertRaises(SystemExit) as error:
                self.run_script(kind, "--init")
            self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.active.exists())

    def test_initlock_is_offline_and_preserves_existing_machine_settings(self) -> None:
        with mock.patch("freecm.seed_store.run", side_effect=AssertionError("network")):
            for kind in ("generic", "cmake"):
                with self.subTest(kind=kind):
                    self.assertEqual(self.run_script(kind, "--initlock"), 0)
                    data = json.loads(self.active.read_text())
                    data["cmakeEnvironment"] = {"LOCAL_SETTING": kind}
                    self.active.write_text(json.dumps(data))
                    before = self.active.read_bytes()
                    self.assertEqual(self.run_script(kind, "--initlock"), 0)
                    self.assertEqual(self.active.read_bytes(), before)
                    self.assertFalse((self.root / "build").exists())

    def test_obsolete_downstream_hooks_fail_instead_of_being_ignored(self) -> None:
        class OldScript(SourceRootWorkflowScript):
            def _cmd_init(self, *, quiet: bool = False) -> int:
                return 0

        with self.assertRaisesRegex(ValueError, "_cmd_initrepo"):
            OldScript(self.facade, repo_display_name="Host")
        self.cmake.cmd_init = lambda: 0
        self.assertEqual(self.run_script("cmake", "--initrepo"), 1)
