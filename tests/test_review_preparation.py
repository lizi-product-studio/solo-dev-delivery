"""Bounded review compiler and public entry; scripted decisions, real acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable-agent-kit'))
from agent_runtime.review_preparation import prepare_review,validate_spec
from agent_runtime.intake import build_context

GOAL='Read numbers.json and write the exact sum to report.json.'
SPEC={'checks':[{'id':'sum_original_numbers','source':'goal','quote':'exact sum',
    'left':{'file':'report.json','mode':'json','pointer':'/total'},
    'right':{'file':'numbers.json','mode':'json','pointer':'/values','reduce':'sum'}}],'unsupported':[]}


class ReviewPreparationTests(unittest.TestCase):
    def test_compiler_executes_real_comparison_and_rejects_invented_or_missing_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir()
            (workspace/'numbers.json').write_text('{"values":[20,22]}',encoding='utf-8')
            context=build_context(GOAL,workspace,['report.json'],'local',max_steps=8)
            def factory(directory):
                def model(request):
                    self.assertEqual([],request['observations']);self.assertNotIn('proposed_plan',request)
                    if directory.name=='author':return {'action':'tool','name':'submit_review_spec','arguments':SPEC}
                    return {'action':'tool','name':'approve_review_spec','arguments':{'verdict':'PASS','missing':[],'reason':'exact original sum is recomputed'}}
                return model
            review=prepare_review(context,{'inputs':['numbers.json'],'outputs':['report.json']},root/'review',factory)
            for value,passed in [(42,True),(43,False),(True,False)]:
                (workspace/'report.json').write_text(json.dumps({'total':value}),encoding='utf-8')
                harness='import importlib.util,unittest,sys;s=importlib.util.spec_from_file_location("review",sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromModule(m));sys.exit(not r.wasSuccessful())'
                result=subprocess.run([sys.executable,'-c',harness,str(review)],cwd=workspace,capture_output=True,text=True)
                self.assertEqual(passed,result.returncode==0,result.stderr)
            bad=json.loads(json.dumps(SPEC));bad['checks'][0]['right']={'literal':42}
            with self.assertRaises(ValueError):validate_spec(bad,GOAL,{'numbers.json':'{}'},['report.json'])
            with self.assertRaises(ValueError):validate_spec(SPEC,GOAL,{'numbers.json':'{}'},['report.json','missing.txt'])
            bad=json.loads(json.dumps(SPEC));bad['checks'][0]['right']=bad['checks'][0]['left']
            with self.assertRaises(ValueError):validate_spec(bad,GOAL,{'numbers.json':'{}'},['report.json'])

    def test_public_goal_entry_prepares_review_before_execution_and_accounts_for_decisions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir()
            (workspace/'numbers.json').write_text('{"values":[20,22]}',encoding='utf-8')
            fake=root/'model.py'
            fake.write_text('''import json,sys,os
sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
r=json.loads(sys.stdin.read().split('Request:\\n',1)[1])
if 'submit_plan' in r['tools']:
 name='submit_plan';args={'inputs':['numbers.json'],'outputs':['report.json'],'steps':['compute sum'],'unknowns':[]}
elif 'submit_review_spec' in r['tools']:
 name='submit_review_spec';args=json.loads('''+repr(json.dumps(SPEC))+''')
elif 'approve_review_spec' in r['tools']:
 name='approve_review_spec';args={'verdict':'UNKNOWN' if os.environ.get('REVIEW_COVERAGE_UNKNOWN') else 'PASS','missing':[],'reason':'recomputes the whole original sum'}
elif r['step']==1:name='read_json';args={'path':'numbers.json'}
elif r['step']==2:name='write_json';args={'path':'report.json','value':{'total':42}}
else:raise AssertionError('host should close after complete artifacts')
d={'action':'tool','name':name,'arguments':args}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(d)}}))
print(json.dumps({'type':'turn.completed'}))
''',encoding='utf-8')
            result=subprocess.run([sys.executable,str(ROOT/'portable-agent-kit/scripts/agent_task.py'),
                '--goal',GOAL,'--workspace',str(workspace),'--allow-write','report.json','--authorization-ref','local',
                '--control-root',str(root/'control'),'--project-store',str(root/'projects'),'--prepare-review','--max-steps','6'],
                env={**os.environ,'CODEX_CLI_PATH':str(fake),'PYTHONUTF8':'1'},capture_output=True,encoding='utf-8',timeout=35)
            self.assertEqual(0,result.returncode,result.stdout+result.stderr)
            receipt=json.loads((root/'control/intake-receipt.json').read_text(encoding='utf-8'))
            self.assertEqual('COMPLETED',receipt['status'])
            self.assertEqual(2,receipt['review_preparation_steps']);self.assertEqual(5,receipt['total_decision_steps'])
            self.assertEqual({'total':42},json.loads((workspace/'report.json').read_text(encoding='utf-8')))
            before=(workspace/'report.json').read_bytes()
            rejected=subprocess.run([sys.executable,str(ROOT/'portable-agent-kit/scripts/agent_task.py'),
                '--goal',GOAL,'--workspace',str(workspace),'--allow-write','report.json','--authorization-ref','local',
                '--control-root',str(root/'rejected'),'--project-store',str(root/'projects'),'--prepare-review','--max-steps','6'],
                env={**os.environ,'CODEX_CLI_PATH':str(fake),'PYTHONUTF8':'1','REVIEW_COVERAGE_UNKNOWN':'1'},capture_output=True,encoding='utf-8',timeout=35)
            self.assertEqual(2,rejected.returncode,rejected.stdout+rejected.stderr)
            receipt=json.loads((root/'rejected/intake-receipt.json').read_text(encoding='utf-8'))
            self.assertEqual('NEEDS_VERIFIER',receipt['status'])
            self.assertFalse((root/'rejected/contract.json').exists())
            self.assertEqual(before,(workspace/'report.json').read_bytes())
