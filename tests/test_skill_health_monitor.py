"""Actual public Runtime, independent assertions and rollback; scripted model."""
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
from agent_runtime.skill_monitor import observe_health


class SkillHealthMonitorTests(unittest.TestCase):
    def activate(self,root,library,version,managed=True):
        spec={'format':'tool-recipe/1','parameters':['path','value'],
              'steps':[{'tool':'write_json','arguments':{'path':{'$input':'path'},'value':{'$input':'value'}}}]}
        if version>1:spec['steps'].append({'tool':'read_json','arguments':{'path':{'$input':'path'}}})
        if managed:spec['health_policy']='quarantine-on-independent-candidate-failure'
        library.register('report-workflow','host-monitored report workflow',spec)
        suite={'format':'skill-artifact-suite/1','cases':[
            {'id':'holdout-'+str(n),'inputs':{},'scope':['report.json'],
             'arguments':{'path':'report.json','value':{'total':n}},
             'assertions':[{'type':'json_pointer_equals','path':'report.json','pointer':'/total','value':n}]} for n in [3,8]]}
        path=root/('suite-v%d.json'%version);path.write_text(json.dumps(suite),encoding='utf-8')
        reviewed=review_skill(library,'report-workflow',path,root/('regression-v%d'%version),reviewer_id='host-holdout-reviewer')
        return activate_reviewed(library,'report-workflow',reviewed)

    def run_task(self,root,library,total):
        root.mkdir();workspace=root/'workspace';workspace.mkdir()
        model=root/'model.py'
        model.write_text('import json\nd='+repr({'action':'skill','name':'report-workflow','arguments':{'path':'report.json','value':{'total':total}}})+
            '\nprint(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(d)}}))\nprint(json.dumps({"type":"turn.completed"}))\n',encoding='utf-8')
        review=root/'review.py';review.write_text('import json,unittest\nfrom pathlib import Path\nclass OriginalGoal(unittest.TestCase):\n def test_total(self):self.assertEqual(42,json.loads(Path("report.json").read_text())["total"])\n',encoding='utf-8')
        contract={'owner':'runtime','candidate':'health-test','authorization_ref':'local-fixture','scope':['report.json'],
            'excluded':['external'],'policy':{'workspace_write':True,'shell_required':False},
            'acceptance':[{'id':'report','expected':'total42','environment':'local','assertion':{'type':'file_exists','path':'report.json'}}]}
        (root/'contract.json').write_text(json.dumps(contract),encoding='utf-8')
        process=subprocess.run([sys.executable,str(ROOT/'portable-agent-kit/scripts/agent_run.py'),'--goal','produce report total42',
            '--workspace',str(workspace),'--memory',str(root/'memory.sqlite3'),'--skills',str(library.database),
            '--contract',str(root/'contract.json'),'--db',str(root/'control.sqlite3'),'--receipt',str(root/'receipt.json'),
            '--review-script',str(review),'--auto-verify','--max-steps','1'],
            env={**os.environ,'CODEX_CLI_PATH':str(model),'PYTHONUTF8':'1'},capture_output=True,encoding='utf-8',timeout=35)
        self.assertTrue((root/'receipt.json').exists(),process.stdout+process.stderr)
        result=json.loads((root/'receipt.json').read_text(encoding='utf-8'))
        health=json.loads(Path(result['invocation_receipt']).with_suffix('.skill-health.json').read_text(encoding='utf-8'))
        return process,result,health

    def test_new_failure_restores_only_previously_observed_passing_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);library=SkillLibrary(root/'skills.sqlite3')
            self.activate(root,library,1)
            process,passed,health=self.run_task(root/'passed',library,42)
            self.assertEqual(0,process.returncode,process.stderr);self.assertEqual('PASS',health['outcome'])
            self.activate(root,library,2)
            process,failed,health=self.run_task(root/'failed',library,41)
            self.assertEqual(2,process.returncode,process.stderr)
            self.assertEqual('FAIL',health['outcome']);self.assertTrue(health['quarantined']);self.assertEqual(1,health['rolled_back_to'])
            self.assertEqual(1,library.get('report-workflow')['version']);self.assertEqual('ACTIVE',library.get('report-workflow')['status'])
            self.assertTrue(library.monitor_run(failed['invocation_receipt'])['reused'])
            self.assertEqual(2,len(library.health_history()))
            altered=json.loads(json.dumps(failed));altered['events'].append({'action':'tool','decision':{'name':'write_json'},'result':{'written':True}})
            (root/'altered.json').write_text(json.dumps(altered),encoding='utf-8')
            with self.assertRaises(ValueError):observe_health(root/'altered.json')
            self.assertEqual(1,library.get('report-workflow')['version'])

    def test_only_managed_workflow_is_quarantined_when_no_passing_fallback_exists(self):
        for managed in (False,True):
            with self.subTest(managed=managed),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);library=SkillLibrary(root/'skills.sqlite3');self.activate(root,library,1,managed=managed)
                _,_,health=self.run_task(root/'failed',library,41)
                self.assertEqual('FAIL',health['outcome']);self.assertEqual(managed,health['quarantined'])
                self.assertEqual(managed,health['managed_policy']);self.assertIsNone(health['rolled_back_to'])
                self.assertEqual('DISABLED' if managed else 'ACTIVE',library.get('report-workflow')['status'])
