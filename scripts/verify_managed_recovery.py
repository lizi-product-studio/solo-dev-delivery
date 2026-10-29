#!/usr/bin/env python3
"""Real model through start/status/resume/cancel; no manual lease or contract."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from verify_domain_runtime import ROOT,FIXTURES


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--model',required=True)
    parser.add_argument('--effort',default='medium');args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    files={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for folder in ('agent_runtime','control_plane','core','scripts')
           for p in (ROOT/'portable-agent-kit'/folder).glob('*.py')}
    write(root/'protocol.json',{'format':'managed-recovery-acceptance/1','requested_model':args.model,
                               'effort':args.effort,'source_hashes':files,'synthetic_task':True})
    task=next(t for t in json.loads((FIXTURES/'manifest.json').read_text(encoding='utf-8'))['tasks'] if t['id']=='data')
    goal=(FIXTURES/task['goal_path']).read_text(encoding='utf-8')
    def execute(label,argv):
        started=time.monotonic()
        process=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'portable-agent-kit/scripts/agent_task.py'),*argv],
                               capture_output=True,encoding='utf-8',timeout=1200)
        (root/(label+'.stdout.log')).write_text(process.stdout,encoding='utf-8')
        (root/(label+'.stderr.log')).write_text(process.stderr,encoding='utf-8')
        write(root/(label+'.process.json'),{'exit_code':process.returncode,'arguments':argv,
                                          'elapsed_seconds':round(time.monotonic()-started,3)})
        return json.loads(process.stdout)
    def setup(name,plan=None):
        folder=root/name;folder.mkdir();workspace=folder/'workspace'
        shutil.copytree(FIXTURES/task['workspace'],workspace)
        review=folder/'review.py';review.write_bytes((FIXTURES/task['review']).read_bytes())
        argv=['--goal',goal,'--workspace',str(workspace),'--control-root',str(folder/'control'),
              '--project-store',str(root/'project-store'),'--allow-write','summary.json',
              '--authorization-ref','user-authorized-local-recovery-acceptance','--review-script',str(review),
              '--model',args.model,'--effort',args.effort,'--isolate-model-config','--max-steps','16','--steps-per-run','1']
        if plan is not None:argv+=['--plan',str(plan)]
        return folder,argv
    folder,argv=setup('resumed')
    initial=execute('start',argv)
    status=execute('status',['status','--control-root',str(folder/'control')])
    resumed=execute('resume',['resume','--control-root',str(folder/'control')]) if status.get('can_resume') else {}
    runtime_path=folder/'control/receipt.json'
    runtime=json.loads(runtime_path.read_text(encoding='utf-8')) if runtime_path.exists() else {}
    reads={e['decision'].get('arguments',{}).get('path') for e in runtime.get('events',[])
           if e['action']=='tool' and e['decision'].get('name') in {'read_file','read_json'}}
    good_resume=(initial['status']=='PAUSED' and status.get('can_resume') and resumed.get('status')=='COMPLETED'
                 and initial['task_id']==resumed['task_id'] and set(task['inputs'])<=reads
                 and runtime['independent_reviews'][-1]['status']=='CHALLENGE_PASS')
    cancel_folder,cancel_argv=setup('cancelled',folder/'control/plan.json')
    cancelled_start=execute('cancel-start',cancel_argv)
    def snapshot():return {p.relative_to(cancel_folder/'workspace').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in (cancel_folder/'workspace').rglob('*') if p.is_file()}
    before=snapshot();calls_before=len(list((cancel_folder/'control/model-events').rglob('process.json')))
    cancelled=execute('cancel',['cancel','--control-root',str(cancel_folder/'control'),'--reason','recovery acceptance cancellation'])
    terminal=execute('cancel-status',['status','--control-root',str(cancel_folder/'control')])
    good_cancel=(cancelled_start['status']=='PAUSED' and cancelled['status']=='CANCELLED' and terminal['status']=='CANCELLED'
                 and before==snapshot() and calls_before==len(list((cancel_folder/'control/model-events').rglob('process.json'))))
    stable=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in files.items())
    final={'status':'PASS' if good_resume and good_cancel and stable else 'INCOMPLETE',
           'resume_pass':bool(good_resume),'cancel_pass':bool(good_cancel),'source_unchanged':stable,
           'same_resume_task_id':initial['task_id']==resumed.get('task_id'),'execution_steps':runtime.get('steps'),
           'planning_steps':initial['planning']['decision_steps'],'cancel_additional_model_calls':len(list((cancel_folder/'control/model-events').rglob('process.json')))-calls_before,
           'limits':'Real requested model on synthetic data. Graceful cross-process recovery, not an unattended soak or arbitrary crash proof.'}
    write(root/'result.json',final);print(json.dumps(final),flush=True)
    return 0 if final['status']=='PASS' else 2


if __name__=='__main__':raise SystemExit(main())
