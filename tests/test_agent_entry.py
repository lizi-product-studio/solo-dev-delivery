"""CLI contract tests with a labelled model transport double."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
CLI=ROOT/'portable-agent-kit/scripts/agent_run.py'


class AgentEntryTests(unittest.TestCase):
    def invoke(self, root, *, contract=True, skip_write=False, output_path='report.json',authorize_write=True):
        workspace=root/'workspace';workspace.mkdir()
        fake=root/'model.py'
        fake.write_text('''import json,sys
req=json.loads(sys.stdin.read().split('Request:\\n',1)[1])
if req['step']==1 and not SKIP:
    decision={'action':'tool','name':'write_file','arguments':{'path':'report.json','text':'{"total":42}'}}
else:
    decision={'action':'finish','answer':'done'}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(decision)}}))
print(json.dumps({'type':'turn.completed'}))
'''.replace('SKIP',str(skip_write)).replace("'path':'report.json'", "'path':"+repr(output_path)),encoding='utf-8')
        argv=[sys.executable,str(CLI),'--goal','produce report', '--provider','codex',
              '--workspace',str(workspace),'--memory',str(root/'memory.sqlite3'),
              '--receipt',str(root/'receipt.json')]
        if contract:
            task={'owner':'agent-runtime','candidate':'fixture','scope':['report.json'],
                  'excluded':['external'],'authorization_ref':'local-test',
                  'policy':{'workspace_write':authorize_write,'shell_required':False},'acceptance':[{
                      'id':'total','expected':'total=42','environment':'local',
                      'assertion':{'type':'json_pointer_equals','path':'report.json',
                                   'pointer':'/total','value':42}}]}
            (root/'contract.json').write_text(json.dumps(task),encoding='utf-8')
            argv+=['--db',str(root/'control.sqlite3'),'--contract',str(root/'contract.json')]
        proc=subprocess.run(argv,env={**os.environ,'CODEX_CLI_PATH':str(fake)},
                            capture_output=True,encoding='utf-8',timeout=15)
        self.assertTrue((root/'receipt.json').exists(),proc.stderr)
        return proc,json.loads((root/'receipt.json').read_text(encoding='utf-8'))

    def test_entry_runs_model_tool_readback_and_control_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            proc,receipt=self.invoke(Path(temp))
            self.assertEqual(0,proc.returncode,proc.stderr)
            self.assertEqual('COMPLETED',receipt['control_state']['status'])
            self.assertEqual(2,receipt['steps'])

    def test_missing_artifact_or_missing_contract_cannot_complete(self):
        for contract in (True,False):
            with self.subTest(contract=contract), tempfile.TemporaryDirectory() as temp:
                proc,receipt=self.invoke(Path(temp),contract=contract,skip_write=True)
                self.assertEqual(2,proc.returncode)
                self.assertIn(receipt['status'],('CONTROL_BLOCKED','UNVERIFIED'))

    def test_contract_scope_blocks_unrequested_file_and_parent_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            proc,receipt=self.invoke(root,output_path='unrequested/other.json')
            self.assertEqual(2,proc.returncode)
            self.assertFalse((root/'workspace/unrequested').exists())
            self.assertEqual('FAILED',receipt['events'][0]['result']['status'])

    def test_read_only_contract_does_not_grant_write_file_permission(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);proc,receipt=self.invoke(root,authorize_write=False)
            self.assertEqual(2,proc.returncode)
            self.assertFalse((root/'workspace/report.json').exists())
            self.assertIn('authorized contract',receipt['events'][0]['result']['error'])


if __name__=='__main__':unittest.main()
