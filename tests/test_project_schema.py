from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from petkit.project import PROJECT_FILE, init_project, read_json, validate_project


class ProjectSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project_dir = init_project(
            self.root / "pets",
            "schema-test",
            "Schema Test",
            "Synthetic schema fixture.",
            "Synthetic pet.",
            "Synthetic style.",
        )
        self.project = read_json(self.project_dir / PROJECT_FILE)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_initialized_project_matches_packaged_schema(self) -> None:
        validate_project(self.project)

    def test_schema_rejects_invalid_project_metadata(self) -> None:
        cases = {
            "empty display name": ("display_name", "", "display_name"),
            "empty description": ("description", "", "description"),
            "invalid status": ("status", "not-a-status", "status"),
            "invalid timestamp": ("created_at", "not-a-date", "created_at"),
            "non-RFC3339 timestamp": (
                "created_at",
                "2026-07-26 12:00:00+00:00",
                "created_at",
            ),
            "excessive chroma threshold": (
                ("generation", "chroma_threshold"),
                500,
                "generation.chroma_threshold",
            ),
            "invalid current build": ("current_build", "latest", "current_build"),
        }
        for label, (path, value, expected_location) in cases.items():
            with self.subTest(label=label):
                project = copy.deepcopy(self.project)
                if isinstance(path, tuple):
                    project[path[0]][path[1]] = value
                else:
                    project[path] = value
                with self.assertRaisesRegex(ValueError, expected_location):
                    validate_project(project)

    def test_schema_rejects_missing_look_metadata(self) -> None:
        project = copy.deepcopy(self.project)
        del project["look"]
        with self.assertRaisesRegex(ValueError, "<root>"):
            validate_project(project)

    def test_old_contract_version_retains_upgrade_guidance(self) -> None:
        project = copy.deepcopy(self.project)
        project["contract_version"] = 1
        with self.assertRaisesRegex(ValueError, "run upgrade-project"):
            validate_project(project)


if __name__ == "__main__":
    unittest.main()
