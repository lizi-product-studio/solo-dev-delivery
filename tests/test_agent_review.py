"""Independent unittest rejection/repair on the public CLI; model double labelled."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class AgentReviewTests(unittest.TestCase):
    def invoke(self,root,repair):
        workspace=root/'workspace';workspace.mkdir()
        (workspace/'TASK.md').write_text('Produce result.json with a correct total.',encoding='utf-8')
        model=root/'model.py'
        model.write_text('''import json,sys
req=json.loads(sys.stdin.read().split('Request:\\n',1)[1])
if req['step']==1:
 d={'action':'tool','name':'write_file','arguments':{'path':'result.json','text':'{"total":41}'}}
elif REPAIR and req['observations'][-1].get('result',{}).get('status')=='REJECTED':
 d={'action':'tool','name':'write_file','arguments':{'path':'result.json','text':'{"total":42}'}}
else:d={'action':'finish','answer':'done'}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(d)}}))
print(json.dumps({'type':'turn.completed'}))
'''.replace('REPAIR',str(repair)),encoding='utf-8')
        review=root/'review.py'
        review.write_text("import json,unittest\nfrom pathlib import Path\nclass Actual(unittest.TestCase):\n def test_total(self):self.assertEqual(42,json.loads(Path('result.json').read_text(encoding='utf-8'))['total'])\n",encoding='utf-8')
        contract={'owner':'runtime','candidate':'fixture','authorization_ref':'test',
            'scope':['result.json'],'inputs':['TASK.md'],'excluded':['external'],
            'policy':{'workspace_write':True,'shell_required':False},
            'acceptance':[{'id':'exists','expected':'result file','environment':'local',
                          'assertion':{'type':'file_exists','path':'result.json'}}]}
        (root/'contract.json').write_text(json.dumps(contract),encoding='utf-8')
        proc=subprocess.run([sys.executable,str(ROOT/'portable-agent-kit/scripts/agent_run.py'),
            '--goal','produce result','--workspace',str(workspace),'--memory',str(root/'m.sqlite3'),
            '--contract',str(root/'contract.json'),'--db',str(root/'c.sqlite3'),
            '--receipt',str(root/'receipt.json'),'--review-script',str(review),'--max-steps','6'],
            env={**os.environ,'CODEX_CLI_PATH':str(model)},capture_output=True,encoding='utf-8',timeout=30)
        self.assertTrue((root/'receipt.json').exists(),proc.stderr)
        return proc,json.loads((root/'receipt.json').read_text(encoding='utf-8'))

    def test_wrong_result_is_rejected_then_model_repairs_and_retests(self):
        with tempfile.TemporaryDirectory() as temp:
            proc,report=self.invoke(Path(temp),True)
            self.assertEqual(0,proc.returncode,proc.stderr)
            self.assertEqual('COMPLETED',report['status'])
            self.assertEqual(['FAIL','CHALLENGE_PASS'],[r['status'] for r in report['independent_reviews']])
            self.assertEqual('REJECTED',report['events'][1]['result']['status'])

    def test_same_wrong_result_never_passes_by_repeating_finish(self):
        with tempfile.TemporaryDirectory() as temp:
            proc,report=self.invoke(Path(temp),False)
            self.assertEqual(2,proc.returncode)
            self.assertEqual('CONTROL_BLOCKED',report['status'])
            self.assertTrue(all(r['status']=='FAIL' for r in report['independent_reviews']))


if __name__=='__main__':unittest.main()
