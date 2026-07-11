from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from freecm.dependency_models import (
    DependencyRootConfig,
    DependencyRootSpec,
    ResolvedDependencyRoots,
)
from freecm.dependency_workflow import (
    DependencyRootWorkflowFacade,
    DependencyRootWorkflowServices,
)


@dataclass(frozen=True)
class WrappedRoots:
    dependency_roots: ResolvedDependencyRoots


class RecordingWorkflow(DependencyRootWorkflowFacade[WrappedRoots]):
    def __init__(
        self,
        config: DependencyRootConfig,
        *,
        services: DependencyRootWorkflowServices | None,
        events: list[str],
    ) -> None:
        self.events = events
        super().__init__(config, services=services)

    def _wrap_dependency_roots(
        self,
        dependency_roots: ResolvedDependencyRoots,
    ) -> WrappedRoots:
        self.events.append("wrap")
        return WrappedRoots(dependency_roots=dependency_roots)

    def _validate_workflow_lock_data(
        self,
        lock_data: Mapping[str, Any],
        *,
        path_label: str | Path,
    ) -> None:
        del lock_data, path_label
        self.events.append("validate-lock")

    def _additional_dependency_root_problems(
        self,
        dependency_roots: WrappedRoots,
    ) -> tuple[str, ...]:
        del dependency_roots
        self.events.append("adapter-verify")
        return ("adapter problem",)


class DependencyRootWorkflowFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo_root = Path(self.tempdir.name)
        spec = DependencyRootSpec("LibA", "LibA", "LIBA_ROOT", ())
        self.events: list[str] = []

        def prepare_assets(_: Path) -> tuple[SimpleNamespace, ...]:
            self.events.append("prepare-assets")
            return (SimpleNamespace(asset_name="AssetBundle"),)

        def require_assets(_: Path) -> tuple[Any, ...]:
            self.events.append("require-assets")
            return ()

        self.workflow = RecordingWorkflow(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=(spec,),
                repo_display_name="HostRepo",
            ),
            services=DependencyRootWorkflowServices(
                prepare_asset_seeds=prepare_assets,
                require_asset_seeds=require_assets,
            ),
            events=self.events,
        )

    def _config(self, repo_name: str = "HostRepo") -> DependencyRootConfig:
        spec = DependencyRootSpec("LibA", "LibA", "LIBA_ROOT", ())
        return DependencyRootConfig(
            repo_root=self.repo_root / repo_name,
            dependency_root_specs=(spec,),
            repo_display_name=repo_name,
        )

    def test_default_services_are_captured_when_each_facade_is_constructed(self) -> None:
        first_prepare = mock.Mock(return_value=())
        second_prepare = mock.Mock(return_value=())
        with mock.patch(
            "freecm.dependency_workflow.prepare_asset_seeds",
            first_prepare,
        ):
            first = RecordingWorkflow(
                self._config("First"),
                services=None,
                events=[],
            )
        with mock.patch(
            "freecm.dependency_workflow.prepare_asset_seeds",
            second_prepare,
        ):
            second = RecordingWorkflow(
                self._config("Second"),
                services=None,
                events=[],
            )

        self.assertIs(first._services.prepare_asset_seeds, first_prepare)
        self.assertIs(second._services.prepare_asset_seeds, second_prepare)
        self.assertIsNot(
            first._services.prepare_asset_seeds,
            second._services.prepare_asset_seeds,
        )

    def test_injected_services_are_instance_scoped(self) -> None:
        first_prepare = mock.Mock(return_value=())
        second_prepare = mock.Mock(return_value=())
        first = RecordingWorkflow(
            self._config("FirstInjected"),
            services=DependencyRootWorkflowServices(
                prepare_asset_seeds=first_prepare,
                require_asset_seeds=mock.Mock(return_value=()),
            ),
            events=[],
        )
        second = RecordingWorkflow(
            self._config("SecondInjected"),
            services=DependencyRootWorkflowServices(
                prepare_asset_seeds=second_prepare,
                require_asset_seeds=mock.Mock(return_value=()),
            ),
            events=[],
        )

        self.assertIs(first._services.prepare_asset_seeds, first_prepare)
        self.assertIs(second._services.prepare_asset_seeds, second_prepare)

    def test_init_validates_lock_before_seed_and_asset_network_work(self) -> None:
        active_path = self.repo_root / "source_roots.lock.jsonc"

        def prepare_closure(*_: Any, **__: Any) -> SimpleNamespace:
            self.events.append("prepare-seeds")
            return SimpleNamespace(topo_order=("LibA",))

        with (
            mock.patch.object(
                self.workflow._manager,
                "ensure_active_lock_file",
                return_value=(active_path, True),
            ),
            mock.patch.object(
                self.workflow._manager,
                "load_lock_file",
                return_value={"dependencies": {}},
            ),
            mock.patch.object(
                self.workflow._manager,
                "prepare_seed_repository_closure",
                side_effect=prepare_closure,
            ),
        ):
            result = self.workflow.init_seed_repositories()

        self.assertEqual(
            self.events,
            ["validate-lock", "prepare-seeds", "prepare-assets"],
        )
        self.assertEqual(
            result, (active_path.resolve(), True, {"LibA": "ready", "asset:AssetBundle": "ready"})
        )

    def test_init_validation_failure_stops_seed_and_asset_preparation(self) -> None:
        active_path = self.repo_root / "source_roots.lock.jsonc"
        with (
            mock.patch.object(
                self.workflow._manager,
                "ensure_active_lock_file",
                return_value=(active_path, False),
            ),
            mock.patch.object(
                self.workflow._manager,
                "load_lock_file",
                return_value={},
            ),
            mock.patch.object(
                self.workflow,
                "_validate_workflow_lock_data",
                side_effect=ValueError("invalid adapter data"),
            ),
            mock.patch.object(
                self.workflow._manager,
                "prepare_seed_repository_closure",
            ) as prepare_seeds,
            self.assertRaisesRegex(ValueError, "invalid adapter data"),
        ):
            self.workflow.init_seed_repositories()

        prepare_seeds.assert_not_called()
        self.assertEqual(self.events, [])

    def test_resolve_is_read_only_and_materialize_requires_assets_after_manager(self) -> None:
        core_roots = SimpleNamespace(repo_root=self.repo_root)

        def materialize(*_: Any, **kwargs: Any) -> SimpleNamespace:
            self.events.append(f"materialize:{kwargs['allow_network']}")
            return core_roots

        with (
            mock.patch.object(
                self.workflow._manager,
                "load_dependency_roots",
                return_value=core_roots,
            ) as load_roots,
            mock.patch.object(
                self.workflow._manager,
                "materialize_dependency_roots",
                side_effect=materialize,
            ),
        ):
            self.workflow.resolve_dependency_roots(materialize=False, allow_network=True)
            self.workflow.materialize_dependency_roots(allow_network=False, quiet=True)
            self.workflow.materialize_dependency_roots(allow_network=True, quiet=False)

        load_roots.assert_called_once_with(self.repo_root.resolve())
        self.assertEqual(
            self.events,
            [
                "wrap",
                "materialize:False",
                "require-assets",
                "wrap",
                "materialize:True",
                "wrap",
            ],
        )

    def test_verify_keeps_core_problems_before_adapter_problems(self) -> None:
        roots = WrappedRoots(dependency_roots=mock.Mock(spec=ResolvedDependencyRoots))

        def validate(_: object) -> list[str]:
            self.events.append("core-verify")
            return ["core problem"]

        with mock.patch.object(
            self.workflow._manager,
            "validate_dependency_roots",
            side_effect=validate,
        ):
            problems = self.workflow.verify_dependency_roots(roots)

        self.assertEqual(self.events, ["core-verify", "adapter-verify"])
        self.assertEqual(problems, ["core problem", "adapter problem"])


if __name__ == "__main__":
    unittest.main()
