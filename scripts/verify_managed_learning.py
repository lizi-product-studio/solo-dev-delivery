#!/usr/bin/env python3
"""Public proposal/holdout/activation and real-model recipe reuse.

Training decisions are scripted; file effects, holdouts, content mutations and
the final requested-model task are actual executions. No AGI learning claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable-agent-kit'))
from agent_runtime.skills import SkillLibrary
from agent_runtime.tools import ToolRegistry
from agent_runtime.file_tools import register_file_tools


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--model',required=True)
    parser.add_argument('--effort',default='medium');args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    sources={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
             for folder in ('agent_runtime','control_plane','core','scripts')
             for p in (ROOT/'portable-agent-kit'/folder).glob('*.py')}
    save(root/'protocol.json',{'format':'managed-learning-acceptance/1','requested_model':args.model,
                              'effort':args.effort,'training_model':'scripted decision double',
                              'source_hashes':sources,'synthetic_task':True})
    library=SkillLibrary(root/'skills.sqlite3');goal='Write the provided verified total'
    for number,total in enumerate((3,8)):
        work=root/('training-'+str(number));work.mkdir()
        def write_file(path,text):
            (work/path).write_bytes(text.encode('utf-8'));return {'written':True,'path':path}
        tools=ToolRegistry();register_file_tools(tools,work,write_file)
        arguments={'path':'training.json','value':{'total':total}}
        result=tools.call('write_json',arguments)
        assert json.loads((work/'training.json').read_text())=={'total':total}
        trace=[{'action':'tool','decision':{'name':'write_json','arguments':arguments},'result':result}]
        library.record_experience('training-'+str(number),goal,trace,success=True)
        save(work/'training-evidence.json',{'decision_source':'scripted','verified_total':total,'events':trace})
    def command(label,argv):
        process=subprocess.run([sys.executable,'-X','utf8',*argv],capture_output=True,encoding='utf-8',timeout=1200)
        (root/(label+'.stdout.log')).write_text(process.stdout,encoding='utf-8')
        (root/(label+'.stderr.log')).write_text(process.stderr,encoding='utf-8')
        save(root/(label+'.process.json'),{'exit_code':process.returncode,'arguments':argv})
        return process,json.loads(process.stdout)
    skill_cli=[str(ROOT/'portable-agent-kit/scripts/skill_manage.py'),'--library',str(root/'skills.sqlite3')]
    _,proposal=command('propose',[*skill_cli,'propose','--goal',goal]);name=proposal['name']
    cases=[]
    for index,total in enumerate((17,29)):
        cases.append({'id':'holdout-'+str(index),'inputs':{},'scope':['report.json'],
                      'arguments':{'step1_path':'report.json','step1_value':{'total':total}},
                      'assertions':[{'type':'json_pointer_equals','path':'report.json','pointer':'/total','value':total}]})
    save(root/'suite.json',{'format':'skill-artifact-suite/1','cases':cases})
    _,review=command('review',[*skill_cli,'review','--name',name,'--suite',str(root/'suite.json'),
                               '--output',str(root/'holdouts'),'--reviewer','independent-host-holdout'])
    _,activation=command('activate',[*skill_cli,'activate','--name',name,'--receipt',review['receipt']])
    work=root/'workspace';work.mkdir();save(work/'input.json',{'total':9})
    reviewer=root/'review.py';reviewer.write_text('import unittest,json\nfrom pathlib import Path\n'
        'class Goal(unittest.TestCase):\n def test_total(self):\n'
        '  self.assertEqual(json.loads(Path("input.json").read_text()),json.loads(Path("report.json").read_text()))\n',encoding='utf-8')
    contract={'owner':'agent-runtime','candidate':'real-skill-transfer','authorization_ref':'user-authorized-local-acceptance',
              'inputs':['input.json'],'scope':['report.json'],'excluded':['external'],
              'policy':{'workspace_write':True,'shell_required':False,'require_input_reads':True},
              'acceptance':[{'id':'total','expected':'preserve total=9','environment':'local',
                            'assertion':{'type':'json_pointer_equals','path':'report.json','pointer':'/total','value':9}}]}
    save(root/'contract.json',contract)
    task_goal='Read input.json, then invoke active skill '+name+' to write report.json with the same total. Use the learned skill rather than direct writing. Finish after persisted readback.'
    process,_=command('reuse',[str(ROOT/'portable-agent-kit/scripts/agent_run.py'),
        '--goal',task_goal,'--workspace',str(work),'--skills',str(root/'skills.sqlite3'),
        '--memory',str(root/'memory.sqlite3'),'--db',str(root/'control.sqlite3'),
        '--contract',str(root/'contract.json'),'--review-script',str(reviewer),
        '--receipt',str(root/'receipt.json'),'--model',args.model,'--effort',args.effort,
        '--isolate-model-config','--max-steps','12'])
    receipt=json.loads((root/'receipt.json').read_text(encoding='utf-8'))
    invocations=[e for e in receipt['events'] if e['action']=='skill' and e['decision']['name']==name]
    stable=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in sources.items())
    passed=(process.returncode==0 and receipt['status']=='COMPLETED' and bool(invocations)
            and receipt['independent_reviews'][-1]['status']=='CHALLENGE_PASS' and stable
            and library.get(name)['uses']>=1 and activation['status']=='ACTIVE')
    result={'status':'PASS' if passed else 'INCOMPLETE','training_decisions':'scripted',
            'holdout_cases':2,'review_status':review['status'],'skill':name,'active_version':activation['version'],
            'real_runtime_status':receipt['status'],'real_skill_calls':len(invocations),'execution_steps':receipt['steps'],
            'source_unchanged':stable,'limits':'Parameterized procedure reuse only; not autonomous algorithm discovery or cross-industry intelligence.'}
    save(root/'result.json',result);print(json.dumps(result),flush=True)
    return 0 if passed else 2


if __name__=='__main__':raise SystemExit(main())
