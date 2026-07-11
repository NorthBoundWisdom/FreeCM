from __future__ import annotations

import argparse
import io
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

from freecm.cli_support import (
    CLI_DATA_ERRORS,
    CLI_INIT_ERRORS,
    CLI_PROCESS_ERRORS,
    run_cli_action,
)
from freecm.dependency_cli import DependencyRootCli
from freecm.dependency_commands import (
    DependencyRootCommandBindings,
    DependencyRootCommands,
)


class CliSupportTests(unittest.TestCase):
    def test_success_is_rendered_outside_action_boundary(self) -> None:
        calls: list[object] = []

        result = run_cli_action(
            lambda: calls.append("action") or "value",
            lambda value: calls.append(("render", value)) or 7,
            error_types=CLI_DATA_ERRORS,
            report_error=lambda error: calls.append(error),
        )

        self.assertEqual(result, 7)
        self.assertEqual(calls, ["action", ("render", "value")])

    def test_expected_errors_are_reported(self) -> None:
        for error in (
            FileNotFoundError("missing"),
            FileExistsError("exists"),
            RuntimeError("runtime"),
            ValueError("value"),
            subprocess.CalledProcessError(1, ["tool"]),
        ):
            with self.subTest(error=type(error).__name__):
                reported: list[BaseException] = []

                def fail(current_error: Exception = error) -> object:
                    raise current_error

                self.assertEqual(
                    run_cli_action(
                        fail,
                        lambda _: 0,
                        error_types=CLI_INIT_ERRORS,
                        report_error=reported.append,
                    ),
                    1,
                )
                self.assertEqual(reported, [error])

    def test_renderer_and_unexpected_exceptions_propagate(self) -> None:
        with self.assertRaisesRegex(KeyError, "render"):
            run_cli_action(
                lambda: "value",
                lambda _: (_ for _ in ()).throw(KeyError("render")),
                error_types=CLI_PROCESS_ERRORS,
                report_error=lambda _: None,
            )
        with self.assertRaisesRegex(KeyError, "action"):
            run_cli_action(
                lambda: (_ for _ in ()).throw(KeyError("action")),
                lambda _: 0,
                error_types=CLI_PROCESS_ERRORS,
                report_error=lambda _: None,
            )

    def test_keyboard_interrupt_and_system_exit_propagate(self) -> None:
        for error in (KeyboardInterrupt(), SystemExit(3)):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                run_cli_action(
                    lambda current_error=error: (_ for _ in ()).throw(current_error),
                    lambda _: 0,
                    error_types=CLI_INIT_ERRORS,
                    report_error=lambda _: None,
                )

    def test_dependency_commands_keep_offline_arguments_and_rendering(self) -> None:
        roots = mock.Mock()
        calls: list[tuple[object, ...]] = []
        commands = DependencyRootCommands(
            DependencyRootCommandBindings(
                load_roots=lambda: roots,
                require_roots=lambda: roots,
                materialize_roots=lambda quiet: calls.append(("materialize", quiet)) or roots,
                pin_ref=lambda dependency, ref: calls.append(("pin", dependency, ref)) or "a" * 40,
                environment_map=lambda _: {"LIBA_ROOT": "/tmp/Lib A"},
                json_dict=lambda _: {"mode": "pinned"},
                report_error=lambda error: calls.append(("error", error)),
                read_error_types=CLI_DATA_ERRORS,
                mutation_error_types=CLI_PROCESS_ERRORS,
            )
        )
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(
                commands.cmd_status(argparse.Namespace(format="shell")),
                0,
            )
            self.assertEqual(
                commands.cmd_materialize(argparse.Namespace(quiet=True)),
                0,
            )
            self.assertEqual(
                commands.cmd_pin(argparse.Namespace(dep="LibA", ref="main")),
                0,
            )

        self.assertEqual(calls, [("materialize", True), ("pin", "LibA", "main")])
        self.assertIn("LIBA_ROOT='/tmp/Lib A'", output.getvalue())
        self.assertIn(f"LibA={'a' * 40}", output.getvalue())

    def test_core_cli_preserves_read_and_mutation_error_boundaries(self) -> None:
        manager = mock.Mock(
            config=SimpleNamespace(repo_display_name="HostRepo"),
            direct_dependency_names=("LibA",),
        )
        cli = DependencyRootCli(manager)
        process_error = subprocess.CalledProcessError(2, ["git", "status"])
        manager.load_dependency_roots.side_effect = process_error

        with self.assertRaises(subprocess.CalledProcessError):
            cli.cmd_show(argparse.Namespace(format="plain"))

        manager.materialize_dependency_roots.side_effect = process_error
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(cli.cmd_materialize(argparse.Namespace()), 1)
        self.assertIn("git", stderr.getvalue())

    def test_graph_json_serialization_error_keeps_action_boundary(self) -> None:
        manager = mock.Mock(
            config=SimpleNamespace(repo_display_name="HostRepo"),
            direct_dependency_names=("LibA",),
        )
        circular: list[object] = []
        circular.append(circular)
        manager.dependency_graph_report.return_value = circular
        cli = DependencyRootCli(manager)
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            self.assertEqual(cli.cmd_graph(argparse.Namespace(format="json")), 1)

        self.assertIn("Circular reference", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
