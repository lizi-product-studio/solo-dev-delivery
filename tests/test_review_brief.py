"""Review handoff behavior, not semantic quality certification."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from review_brief import build_review_brief


class ReviewBriefTests(unittest.TestCase):
    def test_text_change_needs_no_knowledge_or_ui_work(self):
        result = build_review_brief('修正文案', ['copy.txt'], knowledge='/does/not/exist')
        self.assertEqual([], result['focus'])
        self.assertFalse(result['ux_required'])

    def test_ux_handoff_loads_current_lessons_and_preserves_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'methods.json'
            data = {'methods': [{'id': mid, 'rule': 'rule', 'review_probes': [probe],
                      'counterexample': 'do not expand scope', 'sources': ['fixture']}
                     for mid, probe in [('M01', 'first'), ('M03', 'second')]]}
            path.write_text(json.dumps(data))
            first = build_review_brief('目标', ['screen.vue'], ux_required=True, knowledge=path)
            data['methods'][0]['review_probes'] = ['updated']
            path.write_text(json.dumps(data))
            second = build_review_brief('目标', ['screen.vue'], ux_required=True, knowledge=path)
            self.assertEqual(['first'], first['focus'][0]['review_probes'])
            self.assertEqual(['updated'], second['focus'][0]['review_probes'])
            self.assertEqual('do not expand scope', second['focus'][0]['counterexample'])
            self.assertNotIn('verdict', second)

    def test_broken_knowledge_is_not_silently_dropped(self):
        with self.assertRaises(FileNotFoundError):
            build_review_brief('目标', [], ux_required=True, knowledge='/does/not/exist')
        with self.assertRaises(ValueError):
            build_review_brief(' ', [], ux_required=True)

    def test_legacy_knowledge_keeps_old_guidance_and_visible_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'legacy.json'
            data = {'version': 3, 'status': 'CANDIDATE', 'methods': [
                {'id': mid, 'rule': 'rule', 'next_validation': 'legacy probe',
                 'counterexample': 'limit', 'sources': ['old evidence']} for mid in ('M01', 'M03')]}
            path.write_text(json.dumps(data))
            result = build_review_brief('往返检查', ['page.ts'], ux_required=True, knowledge=path)
            self.assertEqual(['legacy probe'], result['focus'][0]['review_probes'])
            self.assertEqual(2, len(result['experience_warnings']))
            self.assertEqual(3, result['knowledge']['version'])
            self.assertEqual('CANDIDATE', result['knowledge']['status'])
            self.assertEqual(64, len(result['knowledge']['sha256']))

    def test_native_cli_does_not_create_queue_in_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(ROOT/'scripts/review_brief.py'),
                '--goal', '核对筛选与返回', '--path', 'screen.vue', '--ux'],
                cwd=directory, capture_output=True, text=True, check=True)
            self.assertEqual(['M01', 'M03'], [m['id'] for m in json.loads(result.stdout)['focus']])
            self.assertEqual([], list(Path(directory).iterdir()))
