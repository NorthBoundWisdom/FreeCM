from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from repomgrcpp import cmake_dependency_builder, cmake_workflow
from repomgrcpp.cmake_dependency_builder import (
    CMakeDependencyBuilder,
    CMakeDependencyBuilderConfig,
    CMakeDependencyBuilderServices,
    CMakeDependencyBuildSpec,
)
from repomgrcpp.cmake_preset_context import CMakeDependencyBuildContext


class CMakeDependencyBuilderTests(unittest.TestCase):
    def test_workflow_facade_reexports_builder_types_and_schema(self) -> None:
        for name in (
            "CMakeDependencyBuildSpec",
            "CMakeDependencyBuilderConfig",
            "CMakeDependencyBuilderServices",
            "CMakeDependencyBuilder",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(cmake_workflow, name),
                    getattr(cmake_dependency_builder, name),
                )
        self.assertEqual(
            cmake_workflow.DEPENDENCY_BUILD_STATE_SCHEMA_VERSION,
            cmake_dependency_builder.DEPENDENCY_BUILD_STATE_SCHEMA_VERSION,
        )

    def test_builder_module_has_no_host_or_network_workflow_state(self) -> None:
        for name in (
            "REPO_ROOT",
            "REPO_DISPLAY_NAME",
            "CMAKE_DEPENDENCY_BUILD_ORDER",
            "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
            "DEPENDENCY_STATE_FILENAME",
            "require_dependency_roots",
            "workspace_mutation_lock",
            "run_command",
            "remove_path",
            "materialize_dependency_roots",
            "prepare_seed_repository_closure",
            "allow_network",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(cmake_dependency_builder, name))

    def test_two_builders_keep_specs_paths_locks_and_services_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            events: list[tuple[str, str, object]] = []
            lock_active = {"A": False, "B": False}

            def make_builder(label: str, dependency_name: str) -> CMakeDependencyBuilder:
                dependency_root = root / f"{label}-source"
                dependency_root.mkdir()
                dependency_roots = SimpleNamespace(
                    closure_order=(dependency_name,),
                    mode="pinned",
                    repo_root=root / f"{label}-repo",
                    resolved_commits={dependency_name: label.lower() * 40},
                    dependency_names_by_parent={},
                    dependency_root_for=lambda name: dependency_root,
                    uses_manual_root_override_for=lambda name: False,
                )

                def require_dependency_roots(*, repo_root: Path) -> object:
                    events.append((label, "require", repo_root))
                    return dependency_roots

                @contextmanager
                def workspace_lock(repo_root: Path):
                    self.assertFalse(lock_active[label])
                    lock_active[label] = True
                    events.append((label, "lock-enter", repo_root))
                    try:
                        yield
                    finally:
                        events.append((label, "lock-exit", repo_root))
                        lock_active[label] = False

                def assert_locked(event: str, value: object) -> None:
                    self.assertTrue(lock_active[label])
                    events.append((label, event, value))

                def configure_dependency_for_context(**kwargs: Any) -> None:
                    assert_locked("configure", kwargs["dependency_name"])

                services = CMakeDependencyBuilderServices(
                    require_dependency_roots=require_dependency_roots,
                    workspace_mutation_lock=workspace_lock,
                    run_command=lambda *args, **kwargs: self.fail(
                        f"{label} unexpectedly ran CMake: {args!r} {kwargs!r}"
                    ),
                    remove_path=lambda path: assert_locked("remove", path),
                    is_managed_dependency_root=lambda repo, dependency: False,
                    has_nested_dependency_workflow=lambda dependency: False,
                    package_repo_root=root / "FreeCM",
                    write_json=lambda path, data: assert_locked("write", path),
                    write_text=lambda path, text: assert_locked("write-text", path),
                    configure_dependency_for_context=configure_dependency_for_context,
                )
                return CMakeDependencyBuilder(
                    CMakeDependencyBuilderConfig(
                        build_order=(
                            CMakeDependencyBuildSpec(
                                dependency_name=dependency_name,
                                uses_c_language=label == "A",
                                cmake_options=(f"-DHOST_{label}=ON",),
                            ),
                        ),
                        state_filename=f".{label.lower()}-state.json",
                    ),
                    services,
                )

            builder_a = make_builder("A", "LibA")
            builder_b = make_builder("B", "LibB")
            context = CMakeDependencyBuildContext(
                preset_name="sample_release",
                generator="Ninja",
                generator_platform="",
                generator_toolset="",
                cmake_executable="cmake",
                build_configurations=("Release",),
                external_prefix_path="",
                cache_variables={"CMAKE_BUILD_TYPE": "Release"},
            )
            repo_a = root / "host-a"
            repo_b = root / "host-b"

            self.assertEqual(
                builder_a.state_file_path(repo_a, context.preset_name).name,
                ".a-state.json",
            )
            self.assertEqual(
                builder_b.state_file_path(repo_b, context.preset_name).name,
                ".b-state.json",
            )
            self.assertEqual(tuple(builder_a.spec_by_name), ("LibA",))
            self.assertEqual(tuple(builder_b.spec_by_name), ("LibB",))

            builder_a.build_dependencies(context, repo_root=repo_a)
            builder_b.build_dependencies(context, repo_root=repo_b)
            builder_a.build_dependencies(context, repo_root=repo_a)

            self.assertEqual(
                [event for event in events if event[1] == "lock-enter"],
                [
                    ("A", "lock-enter", repo_a.resolve()),
                    ("B", "lock-enter", repo_b.resolve()),
                    ("A", "lock-enter", repo_a.resolve()),
                ],
            )
            self.assertEqual(
                [event for event in events if event[1] == "require"],
                [
                    ("A", "require", repo_a.resolve()),
                    ("B", "require", repo_b.resolve()),
                    ("A", "require", repo_a.resolve()),
                ],
            )
            self.assertEqual(
                [event[2] for event in events if event[:2] == ("A", "configure")],
                ["LibA", "LibA"],
            )
            self.assertEqual(
                [event[2] for event in events if event[:2] == ("B", "configure")],
                ["LibB"],
            )

    def test_unlocked_builder_does_not_reacquire_workspace_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            events: list[str] = []
            dependency_roots = SimpleNamespace(
                closure_order=(),
                mode="pinned",
                repo_root=repo_root,
                resolved_commits={},
                dependency_names_by_parent={},
            )

            @contextmanager
            def unexpected_lock(_: Path):
                events.append("lock")
                yield

            builder = CMakeDependencyBuilder(
                CMakeDependencyBuilderConfig(build_order=(), state_filename=".state.json"),
                CMakeDependencyBuilderServices(
                    require_dependency_roots=lambda *, repo_root: dependency_roots,
                    workspace_mutation_lock=unexpected_lock,
                    run_command=lambda *args, **kwargs: None,
                    remove_path=lambda path: None,
                    is_managed_dependency_root=lambda repo, dependency: False,
                    has_nested_dependency_workflow=lambda dependency: False,
                    package_repo_root=repo_root,
                    write_json=lambda path, data: events.append("write"),
                    write_text=lambda path, text: None,
                ),
            )
            context = CMakeDependencyBuildContext(
                preset_name="sample",
                generator="Ninja",
                generator_platform="",
                generator_toolset="",
                cmake_executable="cmake",
                build_configurations=("Release",),
                external_prefix_path="",
                cache_variables={},
            )

            builder.build_dependencies_unlocked(context, repo_root=repo_root)

            self.assertEqual(events, ["write"])
