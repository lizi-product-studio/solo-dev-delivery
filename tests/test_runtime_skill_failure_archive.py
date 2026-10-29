"""The public Runtime archives an actual failed skill, using local doubles."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable-agent-kit'))
from agent_runtime.skills import SkillLibrary
from agent_runtime.skill_management import review_skill,activate_reviewed


class RuntimeSkillFailureArchiveTests(unittest.TestCase):
    def test_unknown_skill_is_archived_without_clearing_unknown_or_disabling(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir()
            library=SkillLibrary(root/'skills.sqlite3')
            spec={'format':'tool-recipe/1','parameters':['path','value'],
                  'steps':[{'tool':'write_json','arguments':{'path':{'$input':'path'},'value':{'$input':'value'}}}]}
            library.register('reviewed-writer','host-supplied parameterized writer',spec)
            suite={'format':'skill-artifact-suite/1','cases':[
                {'id':'holdout-'+str(total),'inputs':{},'scope':['report.json'],
                 'arguments':{'path':'report.json','value':{'total':total}},
                 'assertions':[{'type':'json_pointer_equals','path':'report.json','pointer':'/total','value':total}]}
                for total in (3,8)]}
            (root/'suite.json').write_text(json.dumps(suite),encoding='utf-8')
            review=review_skill(library,'reviewed-writer',root/'suite.json',root/'review',reviewer_id='host-holdout-reviewer')
            active=activate_reviewed(library,'reviewed-writer',review)
            model=root/'model.py'
            model.write_text('import json,sys\n'
                'd={"action":"skill","name":"reviewed-writer","arguments":{"path":"report.json","value":{"total":42}}}\n'
                'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(d)}}))\n'
                'print(json.dumps({"type":"turn.completed"}))\n',encoding='utf-8')
            inject=root/'fault';inject.mkdir()
            (inject/'sitecustomize.py').write_text('from pathlib import Path\noriginal=Path.write_bytes\n'
                'def write(self,data):\n result=original(self,data)\n'
                ' if self.name=="report.json" and self.parent.name=="workspace":raise TimeoutError("return lost after effect")\n'
                ' return result\nPath.write_bytes=write\n',encoding='utf-8')
            contract={'owner':'agent-runtime','candidate':'failure-fixture','authorization_ref':'local-fixture',
                'scope':['report.json'],'excluded':['external'],'policy':{'workspace_write':True,'shell_required':False},
                'acceptance':[{'id':'total','expected':'total42','environment':'local',
                    'assertion':{'type':'json_pointer_equals','path':'report.json','pointer':'/total','value':42}}]}
            (root/'contract.json').write_text(json.dumps(contract),encoding='utf-8')
            env={**os.environ,'CODEX_CLI_PATH':str(model),'PYTHONPATH':str(inject),'PYTHONUTF8':'1'}
            process=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'portable-agent-kit/scripts/agent_run.py'),
                '--goal','use reviewed writer','--workspace',str(workspace),'--memory',str(root/'memory.sqlite3'),
                '--skills',str(root/'skills.sqlite3'),'--contract',str(root/'contract.json'),
                '--db',str(root/'control.sqlite3'),'--receipt',str(root/'receipt.json'),'--max-steps','2'],
                env=env,capture_output=True,encoding='utf-8',timeout=30)
            self.assertEqual(2,process.returncode,process.stderr)
            report=json.loads((root/'receipt.json').read_text(encoding='utf-8'))
            self.assertEqual('NEEDS_RECONCILIATION',report['status'])
            self.assertEqual('UNKNOWN',report['control_state']['status'])
            self.assertEqual({'name':'reviewed-writer','version':active['version'],'spec_sha256':active['spec_sha256']},
                             report['events'][0]['skill_binding'])
            learning=json.loads((root/'receipt.learning.json').read_text(encoding='utf-8'))
            self.assertEqual('UNKNOWN',learning['records'][0]['outcome'])
            self.assertFalse(learning['records'][0]['quarantined'])
            self.assertEqual('ACTIVE',library.get('reviewed-writer')['status'])
            history=library.failure_history('reviewed-writer')
            self.assertEqual(1,len(history))
            immutable=Path(learning['receipt'])
            (root/'receipt.json').write_text('{}',encoding='utf-8')
            self.assertEqual(learning['sha256'],hashlib.sha256(immutable.read_bytes()).hexdigest())
            self.assertTrue(library.record_failure('reviewed-writer',immutable,learning['sha256'])['reused'])


if __name__=='__main__':unittest.main()
