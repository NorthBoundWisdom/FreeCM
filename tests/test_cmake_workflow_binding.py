from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any
from unittest import mock

from repomgrcpp import cmake_workflow
from repomgrcpp.cmake_workflow_binding import (
    CMakeWorkflowScript,
    DependencyRootWorkflowBindings,
)


class FakeDependencyWorkflow:
    def __init__(self, repo_root: Path, label: str, barrier: Barrier | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.label = label
        self.barrier = barrier
        self.lock_active = False
        self.calls: list[tuple[Any, ...]] = []
        lock_data = {
            "depsMode": "pinned",
            "dependencies": {label: {"remote": f"file:///{label}", "commit": label.lower() * 40}},
        }
        self.roots = SimpleNamespace(
            repo_root=self.repo_root,
            mode="pinned",
            closure_order=(label,),
            direct_dependency_names=(label,),
            lock_data=lock_data,
            as_environment_map=lambda: {
                f"{label}_SOURCE_ROOT": str(self.repo_root / "roots" / label)
            },
        )

    def ensure_active_lock_file(self, repo_root: Path | None = None) -> tuple[Path, bool]:
        self._record("ensure", repo_root)
        return self.repo_root / "source_roots.lock.jsonc", False

    def load_lock_file(self, repo_root: Path | None = None) -> dict[str, Any]:
        self._record("load", repo_root)
        return self.roots.lock_data

    def require_dependency_roots(self, repo_root: Path | None = None) -> Any:
        self._record("require", repo_root)
        return self.roots

    def describe_dependency_roots(self, dependency_roots: Any) -> tuple[Any, ...]:
        self._record("describe", dependency_roots.repo_root)
        return ()

    def prepare_nested_dependency_workflows(
        self, dependency_roots: Any, *, repo_root: Path | None = None
    ) -> None:
        self._record("nested", dependency_roots.repo_root, repo_root)

    def prepare_seed_repository_closure(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("public locked seed helper must not be used")

    def materialize_dependency_roots(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("public locked materializer must not be used")

    def _prepare_seed_repository_closure_unlocked(self, repo_root: Path, **_: Any) -> Any:
        self._record("seed", repo_root)
        return SimpleNamespace(topo_order=(self.label,))

    def _materialize_dependency_roots_unlocked(
        self, repo_root: Path, *, allow_network: bool
    ) -> Any:
        self._record("materialize", repo_root, allow_network)
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        return self.roots

    def _record(self, action: str, *values: Any) -> None:
        if action in {
            "ensure",
            "load",
            "seed",
            "materialize",
            "describe",
            "nested",
            "assets:init",
            "assets:update",
            "clangd",
            "host_os",
            "resolve_presets",
            "presets",
        }:
            if not self.lock_active:
                raise AssertionError(f"{self.label} {action} escaped its workspace lock")
        self.calls.append((action, *values))

    @contextmanager
    def workspace_lock(self, repo_root: Path):
        if repo_root.resolve() != self.repo_root:
            raise AssertionError(f"wrong lock root for {self.label}: {repo_root}")
        if self.lock_active:
            raise AssertionError(f"duplicate lock for {self.label}")
        self.lock_active = True
        self.calls.append(("lock:start", repo_root.resolve()))
        try:
            yield
        finally:
            self.calls.append(("lock:end", repo_root.resolve()))
            self.lock_active = False


def bind_fake_script(
    manager: FakeDependencyWorkflow,
    *,
    state_filename: str,
) -> tuple[CMakeWorkflowScript, dict[str, Any]]:
    namespace: dict[str, Any] = {
        "workflow": manager,
        "workspace_mutation_lock": manager.workspace_lock,
        "prepare_asset_seeds": lambda root: manager._record("assets:init", root) or (),
        "require_asset_seeds": lambda root: manager._record("assets:update", root) or (),
        "ensure_clangd_config": lambda root: (
            manager._record("clangd", root) or root / ".clangd",
            False,
        ),
        "host_os_group": lambda: manager._record("host_os", manager.label) or "linux",
        "resolve_preset_models": lambda root, *_: (
            manager._record("resolve_presets", manager.label, root)
            or SimpleNamespace(generated_model={"version": 6, "root": str(root)})
        ),
        "write_generated_cmake_presets": lambda root, _: manager._record("presets", root),
        "print_cli_status": lambda *_args, **_kwargs: None,
        "print_cli_error": lambda error, **kwargs: manager._record(
            "error", manager.label, type(error).__name__, kwargs.get("unexpected", False)
        ),
        "stdout_supports_color": lambda: False,
    }
    spec = cmake_workflow.CMakeDependencyBuildSpec(
        dependency_name=manager.label,
        uses_c_language=True,
        cmake_options=(),
    )
    script = cmake_workflow.bind_cmake_workflow_script(
        namespace,
        repo_root=manager.repo_root,
        repo_display_name=f"Host{manager.label}",
        dependency_build_order=(spec,),
        dependency_state_filename=state_filename,
    )
    return script, namespace


class CMakeWorkflowBindingTests(unittest.TestCase):
    def test_two_bound_contexts_remain_isolated_a_b_a(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            manager_a = FakeDependencyWorkflow(root / "HostA", "A")
            manager_b = FakeDependencyWorkflow(root / "HostB", "B")
            script_a, namespace_a = bind_fake_script(manager_a, state_filename=".a-state.json")
            script_b, namespace_b = bind_fake_script(manager_b, state_filename=".b-state.json")
            facade_snapshot = (
                cmake_workflow.REPO_ROOT,
                cmake_workflow.REPO_DISPLAY_NAME,
                cmake_workflow.CMAKE_DEPENDENCY_BUILD_ORDER,
                cmake_workflow.DEPENDENCY_STATE_FILENAME,
            )

            self.assertEqual(script_a.cmd_update(), 0)
            self.assertEqual(script_b.cmd_update(), 0)
            self.assertEqual(script_a.cmd_update(), 0)

            self.assertEqual(
                namespace_a["dependency_state_file_path"](manager_a.repo_root, "debug").name,
                ".a-state.json",
            )
            self.assertEqual(
                namespace_b["dependency_state_file_path"](manager_b.repo_root, "debug").name,
                ".b-state.json",
            )
            self.assertEqual(
                [
                    spec.dependency_name
                    for spec in namespace_a["ordered_dependency_build_specs"](manager_a.roots)
                ],
                ["A"],
            )
            self.assertEqual(
                [
                    spec.dependency_name
                    for spec in namespace_b["ordered_dependency_build_specs"](manager_b.roots)
                ],
                ["B"],
            )
            self.assertEqual(
                [call[-1] for call in manager_a.calls if call[0] == "materialize"],
                [False, False],
            )
            self.assertEqual(
                [call[-1] for call in manager_b.calls if call[0] == "materialize"],
                [False],
            )
            self.assertEqual(
                facade_snapshot,
                (
                    cmake_workflow.REPO_ROOT,
                    cmake_workflow.REPO_DISPLAY_NAME,
                    cmake_workflow.CMAKE_DEPENDENCY_BUILD_ORDER,
                    cmake_workflow.DEPENDENCY_STATE_FILENAME,
                ),
            )
            self.assertIs(namespace_a["cmd_update"].__self__, script_a)
            self.assertIs(namespace_b["cmd_update"].__self__, script_b)
            self.assertEqual(
                [call[1] for call in manager_a.calls if call[0] == "host_os"],
                ["A", "A"],
            )
            self.assertEqual(
                [call[1] for call in manager_b.calls if call[0] == "host_os"],
                ["B"],
            )
            self.assertEqual(
                [call for call in manager_a.calls if call[0] == "resolve_presets"],
                [
                    ("resolve_presets", "A", manager_a.repo_root),
                    ("resolve_presets", "A", manager_a.repo_root),
                ],
            )
            self.assertEqual(
                [call for call in manager_b.calls if call[0] == "resolve_presets"],
                [("resolve_presets", "B", manager_b.repo_root)],
            )
            self.assertEqual(
                [call for call in manager_a.calls if call[0] == "nested"],
                [
                    ("nested", manager_a.repo_root, manager_a.repo_root),
                    ("nested", manager_a.repo_root, manager_a.repo_root),
                ],
            )
            self.assertEqual(
                [call for call in manager_b.calls if call[0] == "nested"],
                [("nested", manager_b.repo_root, manager_b.repo_root)],
            )

    def test_two_updates_overlap_without_shared_global_state(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            barrier = Barrier(2)
            root = Path(tempdir)
            manager_a = FakeDependencyWorkflow(root / "HostA", "A", barrier)
            manager_b = FakeDependencyWorkflow(root / "HostB", "B", barrier)
            script_a, _ = bind_fake_script(manager_a, state_filename=".a.json")
            script_b, _ = bind_fake_script(manager_b, state_filename=".b.json")

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(lambda script: script.cmd_update(), (script_a, script_b))
                )

            self.assertEqual(results, [0, 0])
            self.assertEqual(manager_a.calls[0][1], manager_a.repo_root)
            self.assertEqual(manager_b.calls[0][1], manager_b.repo_root)

    def test_binding_captures_manager_and_service_objects_once(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            manager = FakeDependencyWorkflow(Path(tempdir) / "HostA", "A")
            script, namespace = bind_fake_script(manager, state_filename=".a.json")
            namespace["workflow"] = object()
            namespace["require_asset_seeds"] = lambda _: (_ for _ in ()).throw(
                AssertionError("late helper replacement leaked into script")
            )

            self.assertEqual(script.cmd_update(), 0)
            self.assertIn(("assets:update", manager.repo_root), manager.calls)

    def test_error_printers_are_captured_per_script(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            manager_a = FakeDependencyWorkflow(root / "HostA", "A")
            manager_b = FakeDependencyWorkflow(root / "HostB", "B")
            script_a, _ = bind_fake_script(manager_a, state_filename=".a.json")
            script_b, _ = bind_fake_script(manager_b, state_filename=".b.json")
            args = SimpleNamespace(init=True, build_dependencies_from_cmake=None, quiet=False)

            for script in (script_a, script_b):
                with (
                    mock.patch.object(script, "parse_args", return_value=args),
                    mock.patch.object(script, "cmd_init", side_effect=RuntimeError("failed")),
                ):
                    self.assertEqual(script.main(), 1)

            self.assertIn(("error", "A", "RuntimeError", False), manager_a.calls)
            self.assertNotIn(("error", "B", "RuntimeError", False), manager_a.calls)
            self.assertIn(("error", "B", "RuntimeError", False), manager_b.calls)

    def test_init_mutations_share_one_workspace_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            manager = FakeDependencyWorkflow(Path(tempdir) / "HostA", "A")
            script, _ = bind_fake_script(manager, state_filename=".a.json")

            self.assertEqual(script.cmd_init(quiet=True), 0)
            self.assertEqual(manager.calls[0], ("lock:start", manager.repo_root))
            self.assertEqual(manager.calls[-1], ("lock:end", manager.repo_root))
            self.assertIn(("seed", manager.repo_root), manager.calls)
            self.assertIn(("assets:init", manager.repo_root), manager.calls)

    def test_unlocked_helpers_are_required_at_bind_time(self) -> None:
        with self.assertRaisesRegex(
            cmake_workflow.WorkflowError,
            "_prepare_seed_repository_closure_unlocked",
        ):
            DependencyRootWorkflowBindings.from_namespace(
                {
                    "ensure_active_lock_file": lambda **_: None,
                    "load_lock_file": lambda **_: {},
                    "require_dependency_roots": lambda **_: None,
                    "describe_dependency_roots": lambda _: (),
                    "prepare_nested_dependency_workflows": lambda _, **__: None,
                    "prepare_seed_repository_closure": lambda **_: None,
                    "materialize_dependency_roots": lambda **_: None,
                }
            )

    def test_facade_import_does_not_import_host_configs_or_add_host_root(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            configs = repo_root / "configs"
            configs.mkdir()
            (configs / "__init__.py").write_text("", encoding="utf-8")
            (configs / "source_roots.py").write_text(
                "raise RuntimeError('host config imported')\n", encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json, sys; "
                        "before=list(sys.path); "
                        "import repomgrcpp.cmake_workflow; "
                        "print(json.dumps({'host': sys.path[0], 'added': "
                        f"{str(repo_root)!r} in sys.path and {str(repo_root)!r} not in before}}))"
                    ),
                ],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn('"added": false', completed.stdout.lower())


if __name__ == "__main__":
    unittest.main()
