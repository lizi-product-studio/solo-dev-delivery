import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable-agent-kit'))
from agent_runtime.skills import SkillLibrary
from agent_runtime.improvement_canary import compare_workflows
from agent_runtime.tools import ToolRegistry


class ImprovementCanaryTests(unittest.TestCase):
    def test_observed_gain_activates_and_changed_canary_blocks_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);library=SkillLibrary(root/'skills.sqlite3')
            baseline={'format':'tool-recipe/1','parameters':['p','body','txt'],'steps':[
                {'tool':'write_text_from_json','arguments':{k:{'$input':v} for k,v in {'source_path':'p','pointer':'body','target_path':'txt'}.items()}}]}
            candidate={'format':'tool-recipe/1','parameters':['p','body','txt','pointer','policy','allowed','field'],
                'steps':[{'tool':'project_json_fields','arguments':{k:{'$input':v} for k,v in {
                    'path':'p','pointer':'pointer','allowed_path':'policy','allowed_pointer':'allowed','field':'field'}.items()}},*baseline['steps']],
                'failure_fingerprint':'host-observed-fixture'}
            library.register('baseline','previous workflow',baseline);library.register('candidate','proposed correction',candidate)
            old=[];new=[]
            for n in (1,2):
                questions=[{'field':v} for v in ('a','b','extra')]
                case={'id':'holdout-'+str(n),'inputs':{'policy.json':json.dumps({'required':['a','b'][:n]})},
                    'initial_outputs':{'draft.json':json.dumps({'questions':questions,'text':'draft'})},
                    'scope':['draft.json','draft.txt'],'assertions':[
                        {'type':'json_pointer_equals','path':'draft.json','pointer':'/questions','value':questions[:n]},
                        {'type':'text_contains','path':'draft.txt','text':'draft'}]}
                args={'p':'draft.json','body':'/text','txt':'draft.txt'}
                old.append({**case,'arguments':args})
                new.append({**case,'arguments':{**args,'pointer':'/questions','policy':'policy.json','allowed':'/required','field':'field'}})
            for name,cases in [('old.json',old),('new.json',new)]:
                (root/name).write_text(json.dumps({'format':'skill-artifact-suite/1','cases':cases}),encoding='utf-8')
            result=compare_workflows(library,'candidate','baseline',root/'new.json',root/'old.json',root/'trial',reviewer_id='independent-host',activate=True)
            self.assertTrue(result['activated']);self.assertEqual('ACTIVE',library.get('candidate')['status'])
            evidence=json.loads(Path(result['receipt']).read_text());self.assertEqual(2,evidence['baseline_failures']);self.assertEqual(0,evidence['candidate_failures'])
            library._check_activation(library.get('candidate'))
            Path(result['receipt']).write_text('{}')
            with self.assertRaises(ValueError):library.execute('candidate',new[0]['arguments'],ToolRegistry())


if __name__=='__main__':unittest.main()
