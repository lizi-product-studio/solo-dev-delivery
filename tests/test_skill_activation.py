import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable-agent-kit'))
from agent_runtime.skills import SkillLibrary
from agent_runtime.tools import ToolRegistry


class SkillActivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.lib=SkillLibrary(self.root/'skills.sqlite3')
        self.row=self.lib.register('writer','write local artifact',{'tool':'write'},owner='implementer')
        self.evidence=self.root/'test.log';self.evidence.write_text('passed\n',encoding='utf-8')
        self.receipt=self.root/'receipt.json'
        self.value={'format':'skill-regression/1','status':'PASS','skill':'writer','version':self.row['version'],
            'spec_sha256':self.row['spec_sha256'],'owner':'implementer','reviewer_id':'reviewer',
            'checks':[{'id':'writes-file','status':'PASS','evidence_refs':[{'uri':'test.log',
                'sha256':hashlib.sha256(self.evidence.read_bytes()).hexdigest()}]}]}

    def save(self):
        self.receipt.write_text(json.dumps(self.value),encoding='utf-8')
        return self.receipt

    def test_ungated_promotion_or_registration_cannot_activate(self):
        with self.assertRaises(ValueError):self.lib.promote('writer')
        with self.assertRaises(ValueError):self.lib.register('bypass','x',{'tool':'write'},status='ACTIVE')
        with self.assertRaises(ValueError):self.lib.activate('writer',{'status':'PASS','skill':'writer'})
        self.assertEqual([],self.lib.list_active())

    def test_evidence_gate_executes_active_recipe_and_survives_restart(self):
        row=self.lib.activate('writer',self.save())
        self.assertEqual('ACTIVE',row['status'])
        lib=SkillLibrary(self.lib.database)
        target=self.root/'out.txt';tools=ToolRegistry()
        tools.register('write',lambda text:target.write_text(text,encoding='utf-8'))
        lib.execute('writer',{'text':'actual output'},tools)
        self.assertEqual('actual output',target.read_text(encoding='utf-8'))
        self.assertEqual(1,lib.list_active()[0]['uses'])

    def test_receipt_rejects_changed_spec_version_owner_or_reviewer(self):
        for key,bad in [('skill','other'),('version',99),('spec_sha256','0'*64),('owner','other'),('reviewer_id','implementer')]:
            old=self.value[key];self.value[key]=bad
            with self.subTest(key=key),self.assertRaises(ValueError):self.lib.activate('writer',self.save())
            self.value[key]=old
        self.assertEqual([],self.lib.list_active())

    def test_stale_receipt_rejected_after_spec_change(self):
        path=self.save()
        new=self.lib.register('writer','updated',{'tool':'read'},owner='implementer')
        self.assertEqual(2,new['version'])
        self.assertNotEqual(self.row['spec_sha256'],new['spec_sha256'])
        with self.assertRaises(ValueError):self.lib.activate('writer',path)

    def test_missing_changed_or_traversing_evidence_blocks_activation(self):
        for uri,sha in [('missing.log','0'*64),('test.log','0'*64),('../test.log','0'*64)]:
            self.value['checks'][0]['evidence_refs']=[{'uri':uri,'sha256':sha}]
            with self.subTest(uri=uri),self.assertRaises(ValueError):self.lib.activate('writer',self.save())
        self.assertEqual('CANDIDATE',self.lib.get('writer')['status'])

    def test_empty_duplicate_or_failed_checks_block_activation(self):
        good=self.value['checks'][0]
        for checks in ([],[good,good],[{**good,'status':'UNKNOWN'}],[{**good,'evidence_refs':[]}]):
            self.value['checks']=checks
            with self.subTest(checks=checks),self.assertRaises(ValueError):self.lib.activate('writer',self.save())

    def test_replacing_active_spec_requires_a_new_gate(self):
        self.lib.activate('writer',self.save())
        unchanged=self.lib.register('writer','same',{'tool':'write'},owner='implementer')
        self.assertEqual('ACTIVE',unchanged['status'])
        changed=self.lib.register('writer','changed',{'tool':'read'},owner='implementer')
        self.assertEqual('CANDIDATE',changed['status'])
        self.assertEqual([],self.lib.list_active())

    def test_legacy_active_rows_are_preserved_but_demoted(self):
        path=self.root/'old.sqlite3'
        db=sqlite3.connect(path)
        db.execute('CREATE TABLE skills(name TEXT PRIMARY KEY,description TEXT NOT NULL,spec TEXT NOT NULL,status TEXT NOT NULL,uses INTEGER NOT NULL DEFAULT 0)')
        db.execute('INSERT INTO skills VALUES(?,?,?,?,?)',('old','legacy',json.dumps({'tool':'write'}),'ACTIVE',4))
        db.commit();db.close()
        lib=SkillLibrary(path)
        self.assertEqual('CANDIDATE',lib.get('old')['status'])
        self.assertEqual(4,lib.get('old')['uses'])
        self.assertEqual(64,len(lib.get('old')['spec_sha256']))


if __name__=='__main__':unittest.main()
