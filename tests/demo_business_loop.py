"""Executable local protocol demo. Customer messages and interpretation are fixtures.

Every command reopens SQLite; Git, tests, delivery gates and archiving really run.
No external messages, real customer projects, model calls or scheduled jobs.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    base=ROOT/'runtime/business-demos';base.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='cycle-',dir=base));repo=work/'repo';repo.mkdir()
    runtime=work/'state';counter=0;trace=[]

    def git(*args):
        return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.PIPE).decode().strip()

    def file(name,data):
        path=work/name;path.write_text(json.dumps(data,ensure_ascii=False,indent=2) if isinstance(data,(dict,list)) else data)
        return str(path)

    def cli(*args):
        nonlocal counter
        result=subprocess.run([sys.executable,str(ROOT/'scripts/delivery.py'),'--runtime',str(runtime),*map(str,args)],
                              capture_output=True,text=True,timeout=60)
        if result.returncode:raise RuntimeError(result.stderr)
        data=json.loads(result.stdout);counter+=1
        (work/f'command-{counter:02}.json').write_text(json.dumps({'command':list(map(str,args)),'result':data},ensure_ascii=False,indent=2))
        trace.append({'command':args[0],'status':data.get('status') if isinstance(data,dict) else None})
        return data

    def event(eid,kind,text,**extras):
        return {'project_id':'demo','event_id':eid,'customer_id':'synthetic-customer','conversation_id':'copy-1',
                'kind':kind,'text':text,**extras}

    git('init');git('config','user.name','Business loop demonstration');git('config','user.email','fixture@example.invalid')
    (repo/'welcome.txt').write_text('旧欢迎语')
    (repo/'.gitignore').write_text('__pycache__/\n')
    (repo/'test_welcome.py').write_text("import unittest\nfrom pathlib import Path\nclass Welcome(unittest.TestCase):\n def test_customer_expected_copy(self): self.assertEqual(Path('welcome.txt').read_text(), '欢迎回来')\n")
    git('add','.');git('commit','-qm','Synthetic baseline with customer outcome test')
    auth=file('mandate-evidence.txt','SYNTHETIC ONLY: current user authorized implementing and testing this business loop. No customer project authorization.')
    policy={'id':'demo','owner':'demo-business-main','authorization_ref':'Current user request: build and verify isolated business loop',
            'authorization_evidence':auth,'repository':str(repo),'exclusive_repository':True,'autonomy':'local_delivery_preapproved',
            'external_send':False,'customer_ids':['synthetic-customer'],'services':{
                'welcome-copy':{'label':'本地欢迎语变更','paths':['welcome.txt'],'excluded':['上线','费用与工期承诺','业务逻辑变化'],
                                'environment':'local_unit','ux_required':False,'risk_signals':['copy_change']}}}
    cli('business-register',file('policy.json',policy))
    s=cli('business-receive',file('request.json',event('m1','request','首页顶部欢迎语不太合适，想改一下。')));cid=s['id']
    facts={'goal':{'value':'调整欢迎语','event_id':'m1','quote':'欢迎语不太合适，想改一下'},
           'target':{'value':'首页顶部欢迎语','event_id':'m1','quote':'首页顶部欢迎语'}}
    decision={'revision':s['revision'],'service_id':'welcome-copy','reasoning':'Fixture interpretation: target is known; exact replacement text is missing.',
              'risks':[],'facts':facts}
    s=cli('business-assess',cid,file('assessment-1.json',decision))
    assert s['status']=='WAITING_INFORMATION' and s['outbox'][-1]['missing_fields']==['expected']
    s=cli('business-receive',file('reply.json',event('m2','reply','改成欢迎回来。')))
    facts['expected']={'value':'欢迎语显示“欢迎回来”','event_id':'m2','quote':'欢迎回来'}
    decision.update(revision=s['revision'],facts=facts,reasoning='Fixture interpretation: all required customer facts are now explicit; only authorized copy changes.')
    s=cli('business-assess',cid,file('assessment-2.json',decision))
    s=cli('business-receive',file('confirm.json',event('m3','confirm','确认这版范围',proposal_id=s['proposal']['id'])))
    tid=s['task_id'];t=cli('show',tid);assert t['status']=='READY'
    assert cli('resource-plan',tid)['units']==[]
    p=cli('review-plan',tid)
    proof=file('preflight.txt','Actual isolated scope examined: welcome.txt only; no business, money, identity, or production changes. Existing test asserts the exact customer string.')
    cli('review-assess',tid,'--actor','demo-business-main',file('preflight.json',{
        'revision':p['revision'],'contract_hash':t['contract_hash'],'risk_reasoning':'Only isolated text change.',
        'scope_reasoning':'welcome.txt only; test defined before implementation.','acknowledged_checks':list(p['checks']),
        'unresolved':[],'evidence':proof}))
    cli('claim',tid,'--owner','demo-business-main')
    (repo/'welcome.txt').write_text('欢迎回来');git('add','welcome.txt');git('commit','-qm','Implement confirmed synthetic customer request')
    p=cli('review-plan',tid)
    cli('review-bind',tid,'--actor','demo-business-main',file('bindings.json',{'revision':p['revision'],'local_only':True,
        'checks':{key:{'argv':[sys.executable,'-m','unittest','-v','test_welcome.py'],'adapter':'unittest','environment':c['environment'],
                       'rationale':'Read actual output file and compare with the exact confirmed customer text.','timeout':20} for key,c in p['checks'].items()}}))
    cli('review-run',tid,'--actor','demo-business-main');p=cli('review-packet',tid)
    checks=p['actual_passes'];assert all(checks.values())
    log=checks['AC:CUSTOMER-1']['evidence']['path']
    cli('review-record',tid,'--actor','demo-business-main',file('review.json',{'revision':p['revision'],'contract_hash':t['contract_hash'],
        'commit':git('rev-parse','HEAD'),'verdict':'PASS','observations':{key:'Verified actual unittest and scoped file diff.' for key in checks},
        'uncovered':[],'resolved_findings':[],'evidence':log}))
    report={'contract_hash':t['contract_hash'],'commit':git('rev-parse','HEAD'),'environment':'local','open_p0_p1':[],
            'checks':[{'id':'CUSTOMER-1','status':'PASS','environment':'local_unit','observed':'welcome.txt reads 欢迎回来','evidence':log}],
            'artifacts':[str(repo/'welcome.txt')]}
    cli('submit',tid,'--owner','demo-business-main',file('report.json',report));cli('finish',tid,'--owner','demo-business-main')
    s=cli('business-show',cid);assert s['status']=='AWAITING_ACCEPTANCE'
    # New work must not invalidate the customer-facing immutable delivery.
    (repo/'welcome.txt').write_text('后续独立开发中的内容');git('add','welcome.txt');git('commit','-qm','Later unrelated fixture work')
    accept=file('accept.json',event('m4','accept','按这版验收通过',delivery_id=s['delivery']['id']))
    s=cli('business-receive',accept);assert s['status']=='CLOSED'
    duplicate=cli('business-receive',accept);assert duplicate==s
    runs=cli('resource-runs',tid);assert runs==[]
    summary={'case_id':cid,'task_id':tid,'business_status':s['status'],'delivery_task_status':cli('show',tid)['status'],
             'simulated_customer_and_interpretation':True,'actual_git_tests_delivery_and_archive':True,
             'extra_owner_approvals_after_project_mandate':0,'child_model_calls':0,'external_messages_sent':0,
             'duplicate_acceptance_idempotent':True,'later_source_change_preserves_delivery':True,
             'trace':trace,'outbox':s['outbox'],'workspace':str(work)}
    file('summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
