#!/usr/bin/env python3
"""Real Codex decision loop paused and resumed by separate CLI processes."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def run(destination):
    root=Path(destination).resolve();root.mkdir(parents=True,exist_ok=False)
    workspace=root/'workspace';workspace.mkdir()
    (workspace/'input.json').write_text('{"values":[13,21,8]}',encoding='utf-8')
    task={'owner':'agent-runtime','candidate':'resume-input-v1','authorization_ref':'local-resume-verification',
          'scope':['output.json'],'excluded':['external systems'],'policy':{'workspace_write':True,'shell_required':False},
          'budget':{'max_steps':20,'max_retries':2},'acceptance':[{'id':'sum','expected':'sum=42',
          'environment':'local','assertion':{'type':'json_pointer_equals','path':'output.json','pointer':'/sum','value':42}}]}
    (root/'contract.json').write_text(json.dumps(task),encoding='utf-8')
    argv=[sys.executable,'-X','utf8',str(ROOT/'portable-agent-kit/scripts/agent_run.py'),
          '--provider','codex','--goal','Read input.json using read_file, sum the values, write output.json with numeric sum, read it back, then finish.',
          '--workspace',str(workspace),'--memory',str(root/'memory.sqlite3'),
          '--db',str(root/'control.sqlite3'),'--contract',str(root/'contract.json'),
          '--task-id','long-task','--max-steps','8']
    try:
        first=subprocess.run(argv+['--steps-per-run','1','--receipt',str(root/'paused.json')],capture_output=True,encoding='utf-8',timeout=300)
        (root/'first.stdout').write_text(first.stdout,encoding='utf-8');(root/'first.stderr').write_text(first.stderr,encoding='utf-8')
        paused=json.loads((root/'paused.json').read_text(encoding='utf-8'))
        if paused['status']!='PAUSED' or paused['control_state']['status']!='PAUSED':
            raise ValueError('first process did not reach a safe pause')
        second=subprocess.run(argv+['--resume','--receipt',str(root/'resumed.json')],capture_output=True,encoding='utf-8',timeout=600)
        (root/'second.stdout').write_text(second.stdout,encoding='utf-8');(root/'second.stderr').write_text(second.stderr,encoding='utf-8')
        report=json.loads((root/'resumed.json').read_text(encoding='utf-8'))
        value=json.loads((workspace/'output.json').read_text(encoding='utf-8'))
        if (second.returncode!=0 or report['status']!='COMPLETED' or value.get('sum')!=42
                or report['events'][:len(paused['events'])]!=paused['events']):
            raise ValueError('resumed task failed acceptance or lost earlier observations')
        result={'status':'PASS','first_status':paused['status'],'resumed_status':report['status'],
                'steps_before_pause':paused['steps'],'total_steps':report['steps'],
                'preserved_observations':len(paused['events']),'artifact':value,'separate_cli_processes':True}
    except Exception as exc:
        result={'status':'FAIL','error':type(exc).__name__+': '+str(exc)}
    (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    return 0 if result['status']=='PASS' else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    raise SystemExit(run(parser.parse_args().output))
