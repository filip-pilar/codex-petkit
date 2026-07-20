from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from petkit.project import init_project


class SafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = Path(__file__).resolve().parents[1]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_invalid_reference_does_not_leave_a_partial_project(self) -> None:
        projects = self.root / "pets"
        with self.assertRaisesRegex(ValueError, "reference image does not exist"):
            init_project(
                projects,
                "incomplete",
                "Incomplete",
                "Must not be created.",
                "test",
                "test",
                references=[self.root / "missing.png"],
            )
        self.assertFalse((projects / "incomplete").exists())

    def test_import_rejects_a_spritesheet_path_outside_the_package(self) -> None:
        package = self.root / "package"
        package.mkdir()
        (package / "pet.json").write_text(
            json.dumps(
                {
                    "id": "escape",
                    "displayName": "Escape",
                    "description": "Malicious path fixture.",
                    "spriteVersionNumber": 2,
                    "spritesheetPath": "../outside.webp",
                }
            ),
            encoding="utf-8",
        )
        projects = self.root / "pets"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "import-package",
                "--package",
                str(package),
                "--root",
                str(projects),
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must remain inside", result.stderr)
        self.assertFalse((projects / "escape").exists())

    def test_look_row_scale_is_uniform_project_local_and_non_overwriting(self) -> None:
        project = init_project(self.root / "pets", "scale-test", "Scale Test", "fixture", "fixture", "fixture")
        source = project / "qa" / "row.png"
        Image.new("RGB", (800, 200), (0, 255, 0)).save(source)
        output = project / "qa" / "row-scaled.png"
        command = [
            sys.executable, "-m", "petkit", "scale-look-row-source",
            "--project", str(project), "--state", "look-b", "--strip", str(source),
            "--factor-x", "1.05", "--factor-y", "1.1", "--output", str(output),
        ]
        subprocess.run(command, cwd=self.repo, check=True, capture_output=True, text=True)
        with Image.open(output) as opened:
            self.assertEqual(opened.size, (840, 220))
        repeated = subprocess.run(command, cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 2)
        self.assertIn("refusing to overwrite", repeated.stderr)


if __name__ == "__main__":
    unittest.main()
