#!/usr/bin/env python3
"""Real Codex task + control plane + independent artifact readback.

Writes a fresh run directory. A zero CLI exit alone never passes this check.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portable-agent-kit'))
from agent_runtime import CodexCliAdapter, RuntimeSession
from control_plane.runtime import DeliveryRuntime
from control_plane.evaluator import verify_task


def run(destination):
    root=Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace=root/'workspace';workspace.mkdir()
    (workspace/'orders.csv').write_text('sku,quantity,unit_price\nA,3,12\nB,5,7\nC,2,19\n',encoding='utf-8')
    task_id='real-'+uuid.uuid4().hex[:12]
    contract={'task_id':task_id,'goal':'汇总 orders.csv 的订单数量和金额，输出 summary.json',
              'owner':'codex-host','candidate':'input-'+hashlib.sha256((workspace/'orders.csv').read_bytes()).hexdigest(),
              'authorization_ref':'user-requested-local-acceptance','scope':['summary.json'],
              'excluded':['external systems','credentials','network writes'],
              'policy':{'workspace_write':False},'budget':{'max_steps':30,'max_retries':2},
              'acceptance':[
                  {'id':'Q','expected':'total_quantity=10','environment':'local',
                   'assertion':{'type':'json_pointer_equals','pointer':'/total_quantity','value':10}},
                  {'id':'M','expected':'total_amount=109','environment':'local',
                   'assertion':{'type':'json_pointer_equals','pointer':'/total_amount','value':109}}]}
    control=DeliveryRuntime(root/'control.sqlite3',evidence_root=workspace)
    state=control.create_task(contract,{'evidence.hash':True,'checkpoint.persist':True,'fs.read':True})
    lease=control.claim(task_id,'codex-host',ttl_seconds=600)
    session=RuntimeSession(control,task_id,lease['lease_id'],evidence_root=workspace,reviewer_id='deterministic-csv-reviewer')
    session.start()
    try:
        events=CodexCliAdapter().run(
            'Complete this one bounded local data task using actual file tools. Read orders.csv; '
            'compute total_quantity=sum(quantity) and total_amount=sum(quantity*unit_price). '
            'Write summary.json with those two numeric fields, then read it back. '
            'Do not delegate, launch other agents, access credentials or operate outside this directory.',
            workspace=workspace,session=session,timeout=240,log_dir=root/'codex')
        output=workspace/'summary.json'
        content=json.loads(output.read_text(encoding='utf-8-sig'))
        if content.get('total_quantity')!=10 or content.get('total_amount')!=109:
            raise ValueError('independent arithmetic readback mismatch')
        for cid in ('Q','M'):
            current=session.snapshot()
            session.evidence({'id':'e-'+cid,'kind':'local-readback','uri':'summary.json',
                              'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
                              'claim':'independent numeric readback','verdict':'PASS','check_id':cid,
                              'environment':'local',**{k:current[k] for k in ('task_id','revision','contract_hash','candidate')}})
        state,response=session.finish()
        review=verify_task(state,control.contract(task_id),workspace,reviewer_id='deterministic-csv-reviewer')
        (root/'review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
        if state['status']!='COMPLETED' or review['status']!='PASS':
            raise ValueError('control-plane completion gate not satisfied')
        result={'status':'PASS','task_id':task_id,'control_status':state['status'],
                'review_status':review['status'],'codex_events':len(events),'result':content,
                'evidence':str(output)}
    except Exception as exc:
        result={'status':'FAIL','task_id':task_id,'error':type(exc).__name__+': '+str(exc),
                'control_status':session.snapshot()['status']}
    (root/'events.json').write_text(json.dumps(control.events(task_id),ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    return 0 if result['status']=='PASS' else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    raise SystemExit(run(parser.parse_args().output))
