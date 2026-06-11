from __future__ import annotations

import unittest
from pathlib import Path

from freecm.dependency_models import (
    DependencyPin,
    DependencyRootSpec,
    ResolvedDependencyRoots,
    dependency_commit_changes,
    manual_root_override_path,
)


class DependencyModelTests(unittest.TestCase):
    def test_resolved_dependency_roots_has_no_project_specific_root_helpers(self) -> None:
        helper_names = {
            name
            for name, value in vars(ResolvedDependencyRoots).items()
            if isinstance(value, property)
        }

        self.assertFalse(
            {
                helper_name
                for helper_name in helper_names
                if helper_name.endswith("_dependency_root")
            }
        )

    def test_dependency_commit_changes_reports_only_changed_direct_dependencies(self) -> None:
        before = {
            "dependencies": {
                "LibA": {"commit": "old-a"},
                "LibB": {"commit": "same-b"},
            }
        }
        after = {
            "dependencies": {
                "LibA": {"commit": "new-a"},
                "LibB": {"commit": "same-b"},
            }
        }

        changes = dependency_commit_changes(before, after, ("LibA", "LibB"))

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].dependency_name, "LibA")
        self.assertEqual(changes[0].old_commit, "old-a")
        self.assertEqual(changes[0].new_commit, "new-a")

    def test_resolved_dependency_roots_json_includes_repo_name_and_manual_mode(self) -> None:
        repo_root = Path("/tmp/freecm-host")
        lock_data = {
            "depsMode": "manual",
            "depsManualPath": {
                "LibA": "/tmp/manual-liba",
            },
            "dependencies": {
                "LibA": {
                    "commit": "locked-a",
                },
            },
        }
        spec = DependencyRootSpec(
            dependency_name="LibA",
            repo_name="RepoA",
            env_key="LIBA_ROOT",
            required_relative_paths=(),
        )
        pin = DependencyPin(
            dependency_name="LibA",
            repo_name="RepoA",
            remote="https://example.invalid/repo-a.git",
            commit="locked-a",
            latest_ref=None,
            declared_by_root=True,
            env_key="LIBA_ROOT",
            required_relative_paths=(),
        )
        resolved = ResolvedDependencyRoots(
            mode="manual",
            repo_root=repo_root,
            lock_data=lock_data,
            direct_dependency_names=("LibA",),
            dependency_pins_by_name={"LibA": pin},
            seed_repositories_by_dependency={
                "LibA": repo_root / "build" / "dependency_seed_repos" / "RepoA"
            },
            dependency_roots_by_name={"LibA": Path("/tmp/manual-liba")},
            resolved_commits_by_dependency={"LibA": "locked-a"},
            dependency_names_by_parent={},
            dependency_declarations_by_name={"LibA": (pin.declaration(),)},
            closure_order=("LibA",),
            dependency_root_specs=(spec,),
        )

        data = resolved.as_json_dict()

        manual_root = Path("/tmp/manual-liba").resolve()

        self.assertEqual(manual_root_override_path(lock_data, "LibA", "manual"), manual_root)
        self.assertEqual(data["roots"], {"LIBA_ROOT": str(Path("/tmp/manual-liba"))})
        self.assertEqual(data["dependencies"]["LibA"]["repoName"], "RepoA")
        self.assertEqual(data["dependencies"]["LibA"]["mode"], "manual")


if __name__ == "__main__":
    unittest.main()
