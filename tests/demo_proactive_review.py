"""Executable failure-to-delivery demonstration; all code and identities are fixtures."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
BROKEN={
 'images.py':"def remove_main(images):\n return images[1:]\n",
 'refunds.py':"def approve(displayed,current):\n return current\n",
 'orders.py':"def recover(key,status):\n return None if status>=400 else key\ndef display(current,request,rows):\n return rows\n"}
FIXED={
 'images.py':"def remove_main(images):\n return [None]+images[1:]\n",
 'refunds.py':"def approve(displayed,current):\n if displayed!=current: raise ValueError('stale application')\n return current\n",
 'orders.py':"def recover(key,status):\n return key\ndef display(current,request,rows):\n return rows if current==request else None\n"}
TESTS='''import unittest
from images import remove_main
from refunds import approve
from orders import recover,display
class InteractionTests(unittest.TestCase):
 def test_image_role(self): self.assertEqual(remove_main(['main','detail1','detail2']),[None,'detail1','detail2'])
 def test_refund_identity(self):
  with self.assertRaises(ValueError): approve('application-A','application-B')
 def test_unknown_request(self): self.assertEqual(recover('original-request',429),'original-request')
 def test_late_response(self): self.assertIsNone(display('shipped','unpaid',['unpaid-order']))
'''


def main():
    scratch_base=ROOT/'runtime/demo-work';scratch_base.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='proactive-',dir=scratch_base));repo=root/'repo';repo.mkdir()
    evidence=ROOT/'evidence';evidence.mkdir(exist_ok=True)
    events=[]
    def git(*args):return subprocess.run(['git','-C',str(repo),*args],check=True,text=True,capture_output=True).stdout.strip()
    def data(name,value):
        p=root/name;p.write_text(json.dumps(value,ensure_ascii=False,indent=2));return str(p)
    def cli(*args,fail=False):
        p=subprocess.run([sys.executable,str(ROOT/'scripts/delivery.py'),'--runtime',str(root/'state'),*map(str,args)],capture_output=True,text=True)
        if fail:
            if p.returncode==0:raise AssertionError('Expected gate rejection: '+str(args))
            events.append({'command':args[0],'expected_rejection':p.stderr.strip()});return
        if p.returncode:raise RuntimeError(p.stderr)
        v=json.loads(p.stdout);events.append({'command':args[0],**{k:v[k] for k in ('next_action','revision','delivery_allowed','status') if k in v}});return v
    git('init');git('config','user.name','Synthetic review demo');git('config','user.email','demo@example.invalid')
    for p,content in BROKEN.items():(repo/p).write_text(content)
    (repo/'test_flows.py').write_text(TESTS);(repo/'.gitignore').write_text('__pycache__/\n')
    git('add','.');git('commit','-m','Synthetic four failure scenarios')
    brief=root/'brief.md';brief.write_text('主动审核机制隔离演示；非真实客户需求，不含平台或资金操作。')
    t=cli('import',brief);tid=t['id']
    c={'goal':'四类关联操作保持正确语义','repository':str(repo),'owner':'fixture','approval_ref':'当前系统建设中的隔离演示',
       'autonomy':'local_until_delivery','scope':list(BROKEN),'excluded':['客户代码','生产和真实资金'],
       'acceptance':[{'id':k,'expected':v,'environment':'isolated'} for k,v in [('A1','主图删除不改变详情图角色'),('A2','旧页面不能批准另一申请'),('A3','查询限流保留原请求身份'),('A4','旧筛选响应不覆盖当前筛选')]],
       'risk_signals':['payment','permissions','backend_frontend'],'estimated_business_files':3,
       'source_resolutions':[{'id':'manual-brief-review','decision':'只操作隔离示例','evidence':'本次系统建设范围'}],
       'open_business_decisions':[],'repository_idle_confirmed':True,'ux_required':False,'rollback':'删除隔离目录',
       'review_components':[{'id':'images','paths':['images.py'],'depends_on':['shared']},
          {'id':'refunds','paths':['refunds.py'],'depends_on':['shared'],'risk_signals':['payment']},
          {'id':'orders','paths':['orders.py'],'depends_on':['shared']},
          {'id':'shared','paths':['test_flows.py','.gitignore'],'depends_on':[]},
          {'id':'unrelated','paths':['marketing'],'depends_on':[]}]}
    t=cli('approve',tid,data('contract.json',c));p=cli('review-plan',tid)
    cli('claim',tid,'--owner','fixture-implementation',fail=True)
    review_note=root/'assessment.txt';review_note.write_text('SYNTHETIC FIXTURE: inspected local models for image identity, refund identity, recovery and query ordering. No network or production.')
    cli('review-assess',tid,'--actor','fixture-implementation',data('assess.json',{'contract_hash':t['contract_hash'],'revision':p['revision'],
        'risk_reasoning':'The fixture changes object identity, money approval and asynchronous recovery semantics.','scope_reasoning':'Only local fixture modules and their explicit tests.',
        'acknowledged_checks':list(p['checks']),'unresolved':[],'evidence':str(review_note)}))
    cli('claim',tid,'--owner','fixture-implementation')
    def bind():
        p=cli('review-plan',tid)
        return cli('review-bind',tid,'--actor','fixture-implementation',data('bindings.json',{'revision':p['revision'],'local_only':True,'checks':{
           cid:{'argv':[sys.executable,'-m','unittest','-v','test_flows.py'],'adapter':'unittest','environment':v['environment'],
                'rationale':'Run the four explicit synthetic cross-operation invariants; no real services.','timeout':20} for cid,v in p['checks'].items()}}))
    bind();p=cli('review-run',tid,'--actor','fixture-implementation');assert not p['delivery_allowed']
    packet=cli('review-packet',tid);logs=list((root/'state/review-evidence'/tid).glob('*.log'));before=logs[-1]
    text=before.read_text();assert 'FAILED (failures=4)' in text
    (evidence/'proactive-demo-before.log').write_text(text)
    cli('submit',tid,'--owner','fixture-implementation',data('fake-pass.json',{'open_p0_p1':[]}),fail=True)
    definitions=[('F1','P2','object_identity','images','Delete main while detail images exist'),
       ('F2','P1','authorization','refunds','Review A after it is replaced by application B'),
       ('F3','P1','unknown_result','orders','Submission happened, response lost, then query returned 429'),
       ('F4','P2','async_lifecycle','orders','Switch filter while earlier response is delayed')]
    for fid,severity,family,component,sequence in definitions:
        p=cli('review-finding',tid,'--actor','fixture-reviewer',data(fid+'.json',{'id':fid,'severity':severity,'family':family,
           'components':[component],'description':sequence,'reproduction':sequence,'evidence':str(before)}))
    expanded=len(p['checks']);assert not any('unrelated' in f['expanded_components'] for f in p['findings'].values())
    # Each command opens a fresh connection; pending obligations survive between invocations.
    assert cli('review-plan',tid)['findings']['F2']['status']=='OPEN'
    for name,value in FIXED.items():(repo/name).write_text(value)
    git('add','.');git('commit','-m','Fix four synthetic invariants')
    bind();p=cli('review-run',tid,'--actor','fixture-implementation');assert not p['delivery_allowed']
    packet=cli('review-packet',tid)
    pass_run=next(iter(packet['actual_passes'].values()));assert pass_run['tests']==4
    (evidence/'proactive-demo-after.log').write_text(Path(pass_run['evidence']['path']).read_text())
    qa=root/'synthetic-review.txt';qa.write_text('SIMULATED REVIEW IDENTITY FOR CONTROL-PLANE TEST ONLY. Four actual tests ran; this is not a native independent agent review or customer acceptance.')
    result={'revision':p['revision'],'contract_hash':t['contract_hash'],'commit':git('rev-parse','HEAD'),'verdict':'PASS',
        'observations':{cid:'Fixture protocol review linked to actual four-counterexample test execution.' for cid in p['checks']},
        'resolved_findings':list(p['findings']),'uncovered':[],'evidence':str(qa),'independence_evidence':str(qa)}
    cli('review-record',tid,'--actor','fixture-implementation',data('review.json',result),fail=True)
    cli('review-record',tid,'--actor','fixture-reviewer',data('review.json',result))
    patch=root/'change.patch';patch.write_text(git('diff','HEAD~1','HEAD'))
    delivery={'contract_hash':t['contract_hash'],'commit':result['commit'],'environment':'local','open_p0_p1':[],
       'checks':[{'id':a['id'],'status':'PASS','environment':a['environment'],'observed':a['expected'],'evidence':pass_run['evidence']['path']} for a in c['acceptance']],
       'review':{'reviewer':'fixture-reviewer','result':'PASS','evidence':str(qa)},'artifacts':[str(patch)]}
    cli('submit',tid,'--owner','fixture-implementation',data('delivery.json',delivery));finished=cli('finish',tid,'--owner','fixture-implementation')
    output={'kind':'SYNTHETIC_CONTROL_PLANE_AND_EXECUTABLE_COUNTEREXAMPLES','task':tid,'result':finished['status'],
       'actual_failed_tests_before':4,'actual_passed_tests_after':4,'expanded_check_count':expanded,
       'unrelated_component_included':False,'database_reopened_between_all_commands':True,
       'simulated_independent_identity':True,'real_customer_or_platform_validation':False,'events':events,
       'runtime':str(root/'state'),'limits':'审查身份为测试占位，用于验证分离门禁；不证明独立agent已完成真实审查。四个业务反例是真实执行的隔离模型，非校服项目复验。'}
    (evidence/'proactive-review-demo.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in output.items() if k!='events'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
