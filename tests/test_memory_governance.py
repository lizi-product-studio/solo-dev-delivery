import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portable-agent-kit'))
from agent_runtime.memory import MemoryStore


class MemoryGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'memory.sqlite3'

    def test_projects_are_isolated_even_for_identical_queries(self):
        a, b = MemoryStore(self.path, scope='project-a'), MemoryStore(self.path, project_id='project-b')
        a_id = a.add('invoice balance 200', source_refs=['invoice-a.json'])
        b_id = b.add('invoice balance 900', source_refs=['invoice-b.json'])
        self.assertEqual([a_id], [r['id'] for r in a.search('invoice balance')])
        self.assertEqual([b_id], [r['id'] for r in b.search('invoice balance')])
        with self.assertRaisesRegex(ValueError, 'current project'): b.get(a_id)
        with self.assertRaisesRegex(ValueError, 'current project'): b.retract(a_id, 'wrong project')
        with self.assertRaisesRegex(ValueError, 'current project'): b.correct(a_id, 'bad', ['source'])

    def test_expired_and_retracted_memories_do_not_enter_retrieval(self):
        memory = MemoryStore(self.path)
        expired = memory.add('stock expired 30', expires_at=time.time()-1)
        withdrawn = memory.add('stock withdrawn 40', source_refs=['receipt-1'])
        current = memory.add('stock current 50', expires_at='2099-01-01T00:00:00Z')
        memory.retract(withdrawn, 'supplied incorrect source')
        self.assertEqual([current], [r['id'] for r in memory.search('stock')])
        self.assertEqual('stock expired 30', memory.get(expired)['text'])
        self.assertIsNotNone(memory.get(withdrawn)['retracted_at'])
        self.assertEqual('retract', memory.history(withdrawn)[0]['operation'])

    def test_correction_preserves_original_and_links_new_source(self):
        memory = MemoryStore(self.path)
        old = memory.add('采购数量为 3', kind='fact', task_id='task-1', source_refs=['old.csv'])
        replacement = memory.correct(old, '采购数量为 7', [{'uri': 'new.csv', 'sha256': 'a'*64}])
        self.assertEqual([replacement], [r['id'] for r in memory.search('采购数量')])
        self.assertEqual(old, memory.get(replacement)['corrects_id'])
        self.assertEqual(replacement, memory.get(old)['superseded_by'])
        self.assertEqual('采购数量为 3', memory.get(old)['text'])
        self.assertEqual('new.csv', memory.get(replacement)['source_refs'][0]['uri'])
        self.assertEqual(replacement, memory.history(old)[0]['detail']['replacement_id'])
        with self.assertRaises(ValueError): memory.correct(old, 'another correction', ['source'])
        with self.assertRaises(ValueError): memory.correct(replacement, 'unproven correction', [])

    def test_lexical_search_handles_chinese_and_english_without_embeddings(self):
        memory = MemoryStore(self.path)
        chinese = memory.add('库存核对完成，采购数量需要二次检查')
        english = memory.add('WRITE_FILE operation recorded')
        self.assertEqual(chinese, memory.search('核对采购数量')[0]['id'])
        self.assertEqual(english, memory.search('write_file')[0]['id'])
        self.assertEqual([], memory.search(''))

    def test_legacy_database_migration_keeps_rows_in_local_scope(self):
        db = sqlite3.connect(self.path)
        db.execute('''CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,task_id TEXT,text TEXT NOT NULL,data TEXT NOT NULL,created REAL NOT NULL)''')
        db.execute('INSERT INTO memories(kind,task_id,text,data,created) VALUES(?,?,?,?,?)',
                   ('episode', 'legacy-task', 'legacy inventory evidence', json.dumps({'count': 12}), time.time()))
        db.commit(); db.close()
        local = MemoryStore(self.path)
        row = local.search('inventory')[0]
        self.assertEqual('local', row['scope'])
        self.assertEqual({'count': 12}, row['data'])
        self.assertEqual([], row['source_refs'])
        self.assertEqual([], MemoryStore(self.path, scope='other').search('inventory'))
        self.assertEqual('legacy-task', MemoryStore(self.path).get(row['id'])['task_id'])

    def test_sources_are_copied_and_bad_expiries_are_rejected(self):
        memory = MemoryStore(self.path)
        refs = [{'uri': 'source.txt', 'observed': 'test'}]
        memory_id = memory.add('source evidence', source_refs=refs)
        refs[0]['uri'] = 'mutated.txt'
        self.assertEqual('source.txt', memory.get(memory_id)['source_refs'][0]['uri'])
        for expiry in (True, float('inf'), '2026-10-29T00:00:00'):
            with self.subTest(expiry=expiry), self.assertRaises(ValueError):
                memory.add('bad expiry', expires_at=expiry)
        with self.assertRaises(ValueError): MemoryStore(self.path, scope='one', project_id='two')


if __name__ == '__main__': unittest.main()
