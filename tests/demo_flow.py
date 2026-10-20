"""Run a real local repair in an isolated synthetic repo, never a client project."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from review_helpers import assess,bind_all,review_data

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('delivery',ROOT/'scripts/delivery.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)

TESTS='''import unittest
from save_flow import save
class SaveTests(unittest.TestCase):
    def test_success(self):
        writes=[]
        self.assertEqual(save(lambda:writes.append(1),lambda:['saved']), 'saved')
        self.assertEqual(len(writes),1)
    def test_readback_failure_never_rewrites(self):
        writes=[]
        def fail(): raise OSError('read unavailable')
        self.assertEqual(save(lambda:writes.append(1),fail),'saved_refresh_failed')
        self.assertEqual(len(writes),1)
    def test_write_failure_never_claims_saved(self):
        def fail(): raise OSError('write unavailable')
        self.assertEqual(save(fail,lambda:[]),'write_failed')
'''
BROKEN='''def save(write,read):
    try:
        write()
        read()
        return 'saved'
    except OSError:
        write()
        return 'saved'
'''
FIXED='''def save(write,read):
    try:
        write()
    except OSError:
        return 'write_failed'
    try:
        read()
    except OSError:
        return 'saved_refresh_failed'
    return 'saved'
'''

def main():
    demo_root=ROOT/'runtime'/'demo-work'
    demo_root.mkdir(parents=True,exist_ok=True)
    scratch=Path(tempfile.mkdtemp(prefix='solo-demo-',dir=str(demo_root)))
    repo=scratch/'repo';repo.mkdir()
    evidence=ROOT/'evidence';evidence.mkdir(exist_ok=True)
    def git(*args):
        return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True)
    git('init');git('config','user.name','Isolated delivery demo');git('config','user.email','demo@example.invalid')
    (repo/'.gitignore').write_text('__pycache__/\n')
    (repo/'save_flow.py').write_text(BROKEN)
    (repo/'test_save_flow.py').write_text(TESTS)
    git('add','.');git('commit','-m','Synthetic save and refresh fixture')
    q=d.Queue(scratch/'runtime')
    brief=scratch/'brief.md';brief.write_text('隔离演示：修复保存成功但刷新失败时重复写入的问题。非客户需求。')
    t=q.ingest(brief)
    contract={'goal':'隔离示例的保存与刷新错误区分','repository':str(repo),'owner':'demo',
        'approval_ref':'本次搭建请求内的隔离演示，非客户项目批准',
        'autonomy':'local_until_delivery','scope':['save_flow.py'],'excluded':['真实业务','生产','外部调用'],
        'acceptance':[{'id':x,'expected':expect,'environment':'local_unit'} for x,expect in [
            ('AC1','成功保存只写一次'),('AC2','刷新失败不重复写入'),('AC3','写入失败不宣称保存成功')]],
        'risk_signals':['isolated_bug','targeted_test'],'estimated_business_files':1,
        'source_resolutions':[{'id':'manual-brief-review','decision':'隔离演示范围明确','evidence':'当前搭建任务'}],
        'open_business_decisions':[],'repository_idle_confirmed':True,'ux_required':False,
        'rollback':'仅移除隔离演示目录，不影响客户项目'}
    t=q.approve(t['id'],contract)
    assessment=scratch/'assessment.txt';assessment.write_text('Synthetic fixture: inspect save and readback failure independently. No external calls.')
    assess(q,t,'synthetic-demo-executor',assessment)
    q.claim(t['id'],'synthetic-demo-executor')
    before=subprocess.run([sys.executable,'-m','unittest','-v'],cwd=repo,capture_output=True,text=True)
    (evidence/'demo-before.log').write_text('SYNTHETIC ISOLATED DEMO\n'+before.stdout+before.stderr)
    if before.returncode==0:raise RuntimeError('反例未触发，不得继续宣称修复')
    checkpoint=scratch/'checkpoint.txt';checkpoint.write_text('已复现：刷新失败造成重复写入，写失败仍错误重试。下一步修复。')
    q.checkpoint(t['id'],'synthetic-demo-executor',checkpoint)
    q.block(t['id'],'synthetic-demo-executor','人为中断以验证恢复；不存在真实外部阻塞')
    q.db.close();q=d.Queue(scratch/'runtime')
    q.resume(t['id'],'synthetic-demo-executor',checkpoint)
    (repo/'save_flow.py').write_text(FIXED)
    after=subprocess.run([sys.executable,'-m','unittest','-v'],cwd=repo,capture_output=True,text=True)
    log=evidence/'demo-after.log';log.write_text('SYNTHETIC ISOLATED DEMO\n'+after.stdout+after.stderr)
    if after.returncode:raise RuntimeError('修复后测试失败')
    git('add','save_flow.py');git('commit','-m','Separate persisted save from refresh failure')
    deliverable=evidence/'demo-change.patch';deliverable.write_text(git('diff','HEAD~1','HEAD').stdout)
    report={'contract_hash':t['contract_hash'],'commit':d.frozen_commit(repo),'environment':'local','open_p0_p1':[],
        'checks':[{'id':a['id'],'status':'PASS','environment':'local_unit','observed':a['expected'],
                   'evidence':str(log)} for a in contract['acceptance']],
        'artifacts':[str(deliverable)]}
    report_file=evidence/'demo-report.json';report_file.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    bind_all(q,t,'synthetic-demo-executor','test_save_flow.py')
    q.reviews.run(t['id'],'synthetic-demo-executor')
    q.reviews.record(t['id'],'synthetic-demo-executor',review_data(q,t,assessment,report['commit']))
    q.submit(t['id'],'synthetic-demo-executor',report_file)
    result=q.finish(t['id'],'synthetic-demo-executor')
    proposal=scratch/'lesson.json';proposal.write_text(json.dumps({
        'observation':'隔离样例中保存成功但读取失败触发了第二次write；拆分异常边界后3项实际测试通过。',
        'candidate_rule':'分开处理持久化与读回错误，已保存后刷新失败不重复提交。',
        'applies_when':'同步保存后读回的交互','does_not_apply_when':'外部提交结果未知还需要原请求核实机制；本例不验证网络幂等',
        'source_ref':'SYNTHETIC_DEMO_NOT_CUSTOMER_EVIDENCE','next_validation':'真实项目中使用隔离API模拟响应丢失与读回失败',
        'evidence_file':str(log)},ensure_ascii=False))
    lesson=q.learn(t['id'],proposal)
    result={'kind':'SYNTHETIC_ISOLATED_DEMO','status':result['status'],'task_id':t['id'],
        'initial_failure_exit':before.returncode,'after_exit':after.returncode,'actual_tests_after':3,
        'checkpoint_restored':True,'commit':report['commit'],
        'events':[dict(row) for row in q.db.execute('SELECT at,kind FROM events ORDER BY seq')],
        'lesson_status':lesson['status'],'customer_projects_modified':False,'production_actions':False,
        'runtime_location':str(scratch/'runtime'), 'limits':'仅验证受控样例与队列连接，不代表真实客户项目自主交付能力。'}
    (evidence/'demo-trace.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    q.db.close();print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
