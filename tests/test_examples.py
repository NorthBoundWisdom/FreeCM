from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ABC_CHAIN_SCRIPT = REPO_ROOT / "examples" / "abc-chain" / "create-fixture.py"


def run_command(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )


class ExampleFixtureTests(unittest.TestCase):
    def test_abc_chain_fixture_runs_init_update_and_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_root = Path(tempdir) / "abc-chain"
            run_command([sys.executable, str(ABC_CHAIN_SCRIPT), str(output_root)])
            app_root = output_root / "AppA"

            run_command([sys.executable, "configs/source_root_workflow.py", "--init"], cwd=app_root)
            run_command(
                [sys.executable, "configs/source_root_workflow.py", "--update"], cwd=app_root
            )
            graph = run_command(
                [sys.executable, "configs/source_roots.py", "graph", "--format", "dot"],
                cwd=app_root,
            ).stdout

            self.assertIn('"LibB" -> "LibC"', graph)
            self.assertIn('"LibB" -> "LibD"', graph)
            self.assertIn('"LibC" -> "LibD"', graph)
            self.assertTrue((app_root / "build" / "dependency_source_roots" / "LibB").is_dir())
            self.assertTrue((app_root / "build" / "dependency_source_roots" / "LibC").is_dir())
            self.assertTrue((app_root / "build" / "dependency_source_roots" / "LibD").is_dir())

            workflow_text = (app_root / "configs" / "source_root_workflow.py").read_text(
                encoding="utf-8"
            )
            self.assertLess(
                workflow_text.index('dependency_name="LibD"'),
                workflow_text.index('dependency_name="LibC"'),
            )
            self.assertLess(
                workflow_text.index('dependency_name="LibC"'),
                workflow_text.index('dependency_name="LibB"'),
            )


if __name__ == "__main__":
    unittest.main()
