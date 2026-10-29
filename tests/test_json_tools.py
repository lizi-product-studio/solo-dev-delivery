import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class StructuredJsonToolTests(unittest.TestCase):
    def test_invalid_json_feedback_can_be_repaired_with_structured_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir()
            fake=root/'model.py'
            fake.write_text('''import json,sys
sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
req=json.loads(sys.stdin.read().split('Request:\\n',1)[1])
if req['step']==1:d={'action':'tool','name':'write_file','arguments':{'path':'report.json','text':'{invalid JSON'}}
elif req['step']==2:d={'action':'finish','answer':'done'}
elif req['step']==3:
 assert 'JSONDecodeError' in req['observations'][-1]['result']['diagnostics']['checks'][0]['error']
 d={'action':'tool','name':'write_json','arguments':{'path':'report.json','value':{'total':42,'body':'hello\\nworld "quoted" 中文'}}}
elif req['step']==4:d={'action':'tool','name':'read_json','arguments':{'path':'report.json'}}
else:
 assert req['observations'][-1]['result']['data']['status']=='DRAFT_REQUIRES_REVIEW'
 d={'action':'finish','answer':'fixed'}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(d)}}))
print(json.dumps({'type':'turn.completed'}))
'''.replace("'total':42,'body'", "'total':42,'status':'DRAFT_REQUIRES_REVIEW','body'"),encoding='utf-8')
            contract={'owner':'runtime','candidate':'json-fixture','authorization_ref':'test',
                'scope':['report.json'],'excluded':['external'],'policy':{'workspace_write':True,'shell_required':False},
                'acceptance':[{'id':'total','expected':'total=42','environment':'local',
                    'assertion':{'type':'json_pointer_equals','path':'report.json','pointer':'/total','value':42}}]}
            (root/'contract.json').write_text(json.dumps(contract),encoding='utf-8')
            proc=subprocess.run([sys.executable,str(ROOT/'portable-agent-kit/scripts/agent_run.py'),
                '--goal','produce structured JSON','--workspace',str(workspace),'--memory',str(root/'m.sqlite3'),
                '--contract',str(root/'contract.json'),'--db',str(root/'c.sqlite3'),'--receipt',str(root/'receipt.json')],
                env={**os.environ,'CODEX_CLI_PATH':str(fake)},capture_output=True,encoding='utf-8',timeout=15)
            self.assertEqual(0,proc.returncode,proc.stderr)
            report=json.loads((root/'receipt.json').read_text(encoding='utf-8'))
            self.assertEqual('COMPLETED',report['status'])
            self.assertEqual('REJECTED',report['events'][1]['result']['status'])
            self.assertEqual('hello\nworld "quoted" 中文',json.loads((workspace/'report.json').read_text(encoding='utf-8'))['body'])


if __name__=='__main__':unittest.main()
