from __future__ import annotations

import threading
import unittest
from unittest import mock

from freecm.git_repositories import run
from freecm.io_metrics import (
    GitCommandObservation,
    capture_io_operations,
    classify_git_command,
    record_git_command,
)
from tools.performance_baseline import run_io_benchmarks


class IoMetricsTests(unittest.TestCase):
    def test_git_command_classifier_covers_stable_categories(self) -> None:
        cases = (
            (("cmake", "--build", "."), None),
            (("/usr/bin/git", "-C", "/repo", "status", "--porcelain"), ("status", False)),
            (("git", "rev-parse", "--is-inside-work-tree"), ("rev_parse_worktree", False)),
            (
                (
                    "git",
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                    "HEAD",
                ),
                ("rev_parse_repository_state", False),
            ),
            (("git", "rev-parse", "--git-common-dir"), ("rev_parse_common_dir", False)),
            (("git", "rev-parse", "--verify", "abc"), ("rev_parse_verify", False)),
            (("git", "rev-parse", "HEAD"), ("rev_parse_head", False)),
            (("git", "rev-parse", "--show-toplevel"), ("rev_parse_other", False)),
            (("git", "remote", "get-url", "origin"), ("remote_get_url", False)),
            (("git", "remote", "get-url", "upstream"), ("remote_get_url", False)),
            (("git", "show", "HEAD:file"), ("show", False)),
            (("git", "worktree", "prune"), ("worktree_prune", False)),
            (("git", "worktree", "add", "path"), ("worktree_add", False)),
            (("git", "worktree", "list"), ("worktree_other", False)),
            (("git", "clone", "remote", "path"), ("clone", True)),
            (("git", "fetch", "origin"), ("fetch", True)),
            (("git", "ls-remote", "remote"), ("ls_remote", True)),
            (("git", "checkout", "main"), ("checkout", False)),
            (("git", "reset", "--hard"), ("reset", False)),
            (("git", "clean", "-fd"), ("clean", False)),
            (("git", "submodule", "update", "--init"), ("submodule_update", True)),
            (("git", "submodule", "status"), ("submodule_other", False)),
            (("git", "--no-pager", "log", "-1"), ("other", False)),
        )
        for command, expected in cases:
            with self.subTest(command=command):
                observation = classify_git_command(command)
                if expected is None:
                    self.assertIsNone(observation)
                else:
                    self.assertEqual(observation, GitCommandObservation(*expected))

    def test_capture_is_nested_exception_safe_and_context_local(self) -> None:
        with capture_io_operations() as outer:
            record_git_command(("git", "status"))
            with self.assertRaisesRegex(RuntimeError, "stop"):
                with capture_io_operations() as inner:
                    record_git_command(("git", "fetch", "origin"))
                    raise RuntimeError("stop")
            record_git_command(("git", "rev-parse", "HEAD"))

        self.assertEqual(
            outer.git_summary(),
            {"total": 2, "byCategory": {"rev_parse_head": 1, "status": 1}},
        )
        self.assertEqual(inner.git_network_summary()["total"], 1)

        summaries: list[dict[str, object]] = []
        barrier = threading.Barrier(2)

        def worker(command: tuple[str, ...]) -> None:
            with capture_io_operations() as recorder:
                barrier.wait(timeout=5)
                record_git_command(command)
                summaries.append(recorder.git_summary())

        threads = [
            threading.Thread(target=worker, args=(("git", "status"),)),
            threading.Thread(target=worker, args=(("git", "rev-parse", "HEAD"),)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(
            {tuple(summary["byCategory"]) for summary in summaries},
            {("status",), ("rev_parse_head",)},
        )

    def test_failed_git_subprocess_attempt_is_recorded(self) -> None:
        with (
            capture_io_operations() as recorder,
            mock.patch(
                "freecm.git_repositories.subprocess.run",
                side_effect=OSError("cannot start"),
            ),
            self.assertRaisesRegex(OSError, "cannot start"),
        ):
            run(["git", "status"])
        self.assertEqual(
            recorder.git_summary(),
            {"total": 1, "byCategory": {"status": 1}},
        )

    def test_real_io_benchmarks_capture_linear_baseline_and_offline_guarantees(self) -> None:
        report = run_io_benchmarks(dependency_count=3, iterations=1)
        self.assertEqual(report["dependencyCount"], 3)
        self.assertEqual(report["topology"], "chain")
        benchmarks = {item["name"]: item for item in report["benchmarks"]}
        self.assertEqual(
            tuple(benchmarks),
            (
                "seed_preflight_init",
                "offline_closure_discovery",
                "offline_materialize_cold",
                "offline_materialize_warm",
                "dependency_root_verify",
            ),
        )
        for benchmark in benchmarks.values():
            git_commands = benchmark["gitCommands"]
            network_commands = benchmark["gitNetworkCommands"]
            self.assertEqual(
                git_commands["total"],
                sum(git_commands["byCategory"].values()),
            )
            self.assertEqual(
                network_commands["total"],
                sum(network_commands["byCategory"].values()),
            )

        init = benchmarks["seed_preflight_init"]
        self.assertEqual(init["gitCommands"]["total"], 51)
        self.assertEqual(init["gitCommands"]["byCategory"]["status"], 9)
        self.assertEqual(init["gitCommands"]["byCategory"]["rev_parse_worktree"], 12)
        self.assertEqual(
            init["gitNetworkCommands"],
            {"total": 6, "byCategory": {"fetch": 3, "ls_remote": 3}},
        )

        closure = benchmarks["offline_closure_discovery"]
        self.assertEqual(closure["gitNetworkCommands"]["total"], 0)
        self.assertEqual(
            closure["gitCommands"]["byCategory"]["rev_parse_repository_state"],
            3,
        )
        self.assertEqual(closure["gitCommands"]["byCategory"]["remote_get_url"], 3)
        self.assertGreaterEqual(closure["gitCommands"]["byCategory"]["show"], 3)
        self.assertEqual(
            closure["gitCommands"]["byCategory"],
            {"remote_get_url": 3, "rev_parse_repository_state": 3, "show": 3},
        )

        for name in (
            "offline_materialize_cold",
            "offline_materialize_warm",
            "dependency_root_verify",
        ):
            self.assertEqual(benchmarks[name]["gitNetworkCommands"]["total"], 0)
        warm = benchmarks["offline_materialize_warm"]
        self.assertEqual(warm["gitCommands"]["byCategory"]["status"], 3)
        self.assertEqual(
            warm["gitCommands"]["byCategory"]["rev_parse_repository_state"],
            6,
        )
        self.assertEqual(
            benchmarks["offline_materialize_cold"]["gitCommands"]["byCategory"],
            {
                "remote_get_url": 3,
                "rev_parse_repository_state": 3,
                "rev_parse_verify": 3,
                "show": 3,
                "worktree_add": 3,
                "worktree_prune": 3,
            },
        )
        self.assertEqual(
            warm["gitCommands"]["byCategory"],
            {
                "remote_get_url": 3,
                "rev_parse_repository_state": 6,
                "rev_parse_verify": 3,
                "show": 3,
                "status": 3,
                "worktree_prune": 3,
            },
        )
        verify = benchmarks["dependency_root_verify"]
        self.assertEqual(closure["gitCommands"]["total"], 9)
        self.assertEqual(benchmarks["offline_materialize_cold"]["gitCommands"]["total"], 18)
        self.assertEqual(benchmarks["offline_materialize_warm"]["gitCommands"]["total"], 21)
        self.assertEqual(verify["gitCommands"]["total"], 6)
        self.assertEqual(verify["gitCommands"]["byCategory"]["rev_parse_worktree"], 3)
        self.assertEqual(verify["gitCommands"]["byCategory"]["rev_parse_head"], 3)


if __name__ == "__main__":
    unittest.main()
