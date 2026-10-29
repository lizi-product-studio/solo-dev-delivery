"""Failure-to-candidate safety using actual failed local independent suites."""
import json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable-agent-kit'))
from agent_runtime.failure_learning import propose_improvement,observe_failure
from agent_runtime.skills import SkillLibrary
from agent_runtime.tools import ToolRegistry
import test_agent_review as review_fixture


class FailureLearningTests(unittest.TestCase):
    def test_actual_failures_create_only_one_unactivated_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);paths=[]
            fixture=review_fixture.AgentReviewTests()
            for number in range(2):
                run=root/str(number);run.mkdir();fixture.invoke(run,False)
                paths.append(run/'receipt.json')
            library=SkillLibrary(root/'skills.sqlite3');tools=ToolRegistry()
            tools.register('write_json',lambda path,value:None)
            calls=[]
            def factory(directory):
                def model(request):
                    calls.append(request)
                    return {'action':'tool','name':'submit_improvement','arguments':{
                        'summary':'Use structured JSON output','rationale':'Candidate workflow hypothesis for the observed failures',
                        'spec':{'format':'tool-recipe/1','parameters':['path','value'],
                            'steps':[{'tool':'write_json','arguments':{'path':{'$input':'path'},'value':{'$input':'value'}}}]}}}
                return model
            result=propose_improvement(library,paths,factory,tools,root/'proposal')
            self.assertEqual('CANDIDATE',result['status']);self.assertEqual([],library.list_active())
            self.assertTrue(propose_improvement(library,paths,factory,tools,root/'unused')['reused'])
            self.assertEqual(1,len(calls));self.assertFalse((root/'unused').exists())
            with self.assertRaises(ValueError):propose_improvement(library,[paths[0],paths[0]],factory,tools,root/'duplicate')
            fake=json.loads(paths[0].read_text(encoding='utf-8'));fake['status']='COMPLETED'
            (root/'fake.json').write_text(json.dumps(fake),encoding='utf-8')
            with self.assertRaises(ValueError):observe_failure(root/'fake.json')


if __name__=='__main__':unittest.main()
