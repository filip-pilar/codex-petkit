"""Default Astra workflow: real assembly and temporary installation, no production art."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from petkit.build import build_project, review_build, accept_build, install_build, rollback_install, preflight_phase
from petkit.cli import cmd_ingest_row
from petkit.project import load_project, save_project, plan_edit, sha256_file, create_variant, upgrade_project
from tests import test_workflow as fixtures
from tests.helpers import row_strip, replacement_frame


class AstraWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.WorkflowTests()
        cls.fixture.setUp()
        cls.fixture.ingest()
        project_dir, project = load_project(cls.fixture.project)
        project['look'] = {'mechanics': None, 'cardinals': None, 'row_9_approved': False, 'row_9_approval': None}
        save_project(project_dir, project)
        shutil.rmtree(project_dir / 'qa')
        cls.release = build_project(project_dir)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / 'pet'
        shutil.copytree(self.fixture.project, self.project)
        self.build_id = self.release['build_id']
        self.build = self.project / 'builds' / self.build_id

    def tearDown(self):
        self.temp.cleanup()

    def accept(self):
        review_build(self.project, self.build_id, confirm_visual_qa=True, review_note='Synthetic review for deterministic tests only.')
        accept_build(self.project, self.build_id, confirm_visual_qa=True, review_note='Synthetic release.')

    def test_default_build_without_planning_forms_or_review_panels(self):
        record = json.loads((self.build / 'build.json').read_text())
        self.assertEqual(record['review_profile'], 'visual')
        self.assertEqual(record['review_authority_sha256'], {})
        self.assertFalse((self.build / 'qa-private').exists())
        self.assertEqual(len(list((self.build / 'previews').glob('*.gif'))), 11)
        _, project = load_project(self.project)
        self.assertTrue(preflight_phase(self.project, project, 'review')['ok'])
        with self.assertRaisesRegex(ValueError, 'review package'):
            accept_build(self.project, self.build_id, confirm_visual_qa=True, review_note='Unreviewed')
        with self.assertRaisesRegex(ValueError, 'inspection confirmation'):
            review_build(self.project, self.build_id, review_note='No inspection')
        self.accept()
        _, project = load_project(self.project)
        self.assertTrue(preflight_phase(self.project, project, 'install')['ok'])
        self.assertTrue(upgrade_project(self.project)['already_v2'])
        self.assertEqual(load_project(self.project)[1]['accepted_build'], self.build_id)

    def test_cli_review_is_available(self):
        command = [sys.executable, '-m', 'petkit', 'review', '--project', str(self.project),
                   '--build-id', self.build_id, '--confirm-visual-qa', '--review-note', 'Synthetic CLI review.']
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['schema_version'], 4)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            review_build(self.project, self.build_id, confirm_visual_qa=True, review_note='Duplicate')

    def test_inspection_artifact_tampering_rejected_before_review_and_install(self):
        for name in ('contact-sheet.png', 'change-report.json', 'previews/waving.gif'):
            with self.subTest(artifact=name):
                path = self.build / name
                original = path.read_bytes()
                path.write_bytes(original + b'tampered')
                with self.assertRaisesRegex(ValueError, 'immutable build'):
                    review_build(self.project, self.build_id, confirm_visual_qa=True, review_note='Invalid evidence')
                path.write_bytes(original)
        self.accept()
        path = self.build / 'previews/waving.gif'
        path.write_bytes(path.read_bytes() + b'tampered')
        with self.assertRaisesRegex(ValueError, 'immutable build'):
            install_build(self.project, self.root / 'installed')
        self.assertFalse((self.root / 'installed').exists())

    def test_review_binding_and_source_drift_rejected(self):
        review_build(self.project, self.build_id, confirm_visual_qa=True, review_note='Synthetic review')
        review = self.project / 'reviews' / self.build_id / 'review-summary.json'
        payload = json.loads(review.read_text())
        original = review.read_bytes()
        payload['binding']['atlas_sha256'] = '0' * 64
        review.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            accept_build(self.project, self.build_id, confirm_visual_qa=True, review_note='Stale review')
        review.write_bytes(original)
        replacement_frame(self.project / 'source/frames/waving/00.png', self.fixture.contract, (230, 60, 70, 255))
        with self.assertRaisesRegex(ValueError, 'source inputs no longer match'):
            accept_build(self.project, self.build_id, confirm_visual_qa=True, review_note='Drifted source')

    def test_review_failure_does_not_publish_partial_record(self):
        with patch('petkit.build._verify_review_package', side_effect=ValueError('injected validation failure')):
            with self.assertRaisesRegex(ValueError, 'injected'):
                review_build(self.project, self.build_id, confirm_visual_qa=True, review_note='Synthetic')
        self.assertEqual(list((self.project / 'reviews').iterdir()), [])

    def test_scoped_edit_install_and_rollback(self):
        self.accept()
        installed = self.root / 'installed'
        first = install_build(self.project, installed)
        package = Path(first['target'])
        before = (package / 'spritesheet.webp').read_bytes()
        plan_edit(self.project, 'deterministic', 'Change waving only', ['waving'])
        replacement_frame(self.project / 'source/frames/waving/00.png', self.fixture.contract, (230, 60, 70, 255))
        edited = build_project(self.project)
        report = json.loads((Path(edited['build_dir']) / 'change-report.json').read_text())
        self.assertEqual(set(report['changed_states']), {'waving'})
        review_build(self.project, edited['build_id'], confirm_visual_qa=True, review_note='Synthetic changed-row review.')
        accept_build(self.project, edited['build_id'], confirm_visual_qa=True, review_note='Synthetic edit.')
        second = install_build(self.project, installed)
        self.assertNotEqual((package / 'spritesheet.webp').read_bytes(), before)
        self.assertTrue(Path(second['backup']).exists())
        restored = rollback_install(self.project, installed)
        self.assertEqual((package / 'spritesheet.webp').read_bytes(), before)
        self.assertTrue(Path(restored['displaced_backup']).exists())
        with self.assertRaisesRegex(ValueError, 'overlap|contain'):
            install_build(self.project, self.project)

    def test_look_rows_ingest_in_either_order_without_studies(self):
        for state_id in ('look-b', 'look-a'):
            strip = row_strip(self.root / f'{state_id}.png', self.fixture.contract.state(state_id), self.fixture.contract)
            args = SimpleNamespace(project=str(self.project), state=state_id, strip=strip,
                                   chroma_key=None, chroma_threshold=None, method='components')
            with patch('petkit.cli.emit'):
                cmd_ingest_row(args)
        _, project = load_project(self.project)
        self.assertTrue(preflight_phase(self.project, project, 'build')['ok'])

    def test_unrelated_changes_still_rejected(self):
        self.accept()
        plan_edit(self.project, 'generative', 'Wave only', ['waving'])
        replacement_frame(self.project / 'source/frames/failed/00.png', self.fixture.contract, (200, 100, 40, 255))
        with patch('petkit.build.assemble_v2') as assemble:
            with self.assertRaisesRegex(ValueError, 'outside its recorded scope'):
                build_project(self.project)
            assemble.assert_not_called()
