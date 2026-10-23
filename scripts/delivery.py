#!/usr/bin/env python3
"""Local intake, quality gates and bounded on-demand Codex worker dispatch.

The main conversation coordinates work; this CLI does not schedule future runs.

Only Python standard library is needed; importing 开项 JSON also needs Node.
No repository commands from imported text are executed. No deployment operation exists.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vendor' / 'orchestrator'))
sys.path.insert(0, str(ROOT / 'scripts'))
from review_engine import Reviews
from resource_engine import Resources
from business_loop import Business
from task_mode import classify_task_mode

def now():
    return datetime.now(timezone.utc).isoformat()

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()

def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def need(condition, message):
    if not condition:
        raise ValueError(message)

def node_bin():
    result = os.environ.get('SOLO_NODE') or shutil.which('node')
    if result:
        return result
    config = ROOT / 'runtime' / 'machine.json'
    if config.exists():
        result = read_json(config).get('node')
        if result and Path(result).is_file():
            return result
    raise ValueError('缺少 Node；配置 SOLO_NODE 或 runtime/machine.json 的 node 绝对路径')

def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE).decode().strip()

def frozen_commit(repo):
    repo = Path(repo).resolve()
    need(repo.is_dir(), '代码目录不存在')
    need(not git(repo, 'status', '--porcelain'), '交付证据必须绑定干净提交；先保留并提交本任务修改，不得清理用户改动')
    return git(repo, 'rev-parse', 'HEAD')

def artifact(path):
    path = Path(path).resolve()
    need(path.is_file() and path.stat().st_size > 0, '证据文件不存在或为空: ' + str(path))
    return {'path':str(path), 'sha256':file_hash(path)}

def validate_contract(c):
    for key in ('goal', 'repository', 'owner', 'approval_ref', 'autonomy', 'rollback'):
        need(isinstance(c.get(key), str) and bool(c[key].strip()), '缺少任务契约字段: ' + key)
    need(c['autonomy'] == 'local_until_delivery', 'MVP只支持本地交付；上线和资金动作另立授权任务')
    for key in ('scope', 'excluded', 'acceptance', 'risk_signals', 'source_resolutions'):
        need(isinstance(c.get(key), list), '字段必须是列表: ' + key)
    need(c['scope'] and c['excluded'] and c['acceptance'], '范围、排除项、验收不能空')
    need(c.get('open_business_decisions') == [], '还有未决定的经营/范围问题')
    need(c.get('repository_idle_confirmed') is True, '先核对仓库当前写入者并完成交接')
    need(c.get('ux_required') in (True, False), '明确本任务是否需要实际界面验收')
    ids = []
    for ac in c['acceptance']:
        need(isinstance(ac, dict) and ac.get('id') and ac.get('expected') and ac.get('environment'), '验收项需id、可观察预期和环境')
        ids.append(ac['id'])
    need(len(ids) == len(set(ids)), '验收ID重复')
    for row in c['source_resolutions']:
        need(row.get('id') and row.get('decision') and row.get('evidence'), '来源待定项需逐项决定和证据')
    signals = c['risk_signals']
    route = classify_task_mode(project_complexity='Standard', signals=signals,
        estimated_business_files=c.get('estimated_business_files', 4))
    need(route['status'] == 'ROUTED', '范围扩展尚未确认')
    return route

class Queue:
    def __init__(self, runtime):
        self.root = Path(runtime).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.root / 'queue.sqlite3'), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY, source_hash TEXT UNIQUE, source TEXT NOT NULL,
          status TEXT NOT NULL, contract TEXT, contract_hash TEXT,
          repository TEXT, owner TEXT, checkpoint TEXT, reason TEXT,
          evidence TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY, task_id TEXT, at TEXT, kind TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS lessons(
          id TEXT PRIMARY KEY, task_id TEXT, at TEXT, proposal TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_writer_per_repo ON tasks(repository)
          WHERE status IN ('RUNNING', 'REVIEW', 'BLOCKED');
        ''')
        self.db.commit()
        self.reviews = Reviews(self)
        self.resources = Resources(self)
        self.business = Business(self)

    @contextlib.contextmanager
    def transaction(self):
        if self.db.in_transaction:
            self._savepoint_counter=getattr(self,'_savepoint_counter',0)+1
            name='nested_'+str(self._savepoint_counter)
            self.db.execute('SAVEPOINT '+name)
            try:
                yield
                self.db.execute('RELEASE SAVEPOINT '+name)
            except Exception:
                self.db.execute('ROLLBACK TO SAVEPOINT '+name)
                self.db.execute('RELEASE SAVEPOINT '+name)
                raise
            return
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def event(self, tid, kind, detail):
        self.db.execute('INSERT INTO events(task_id,at,kind,detail) VALUES (?,?,?,?)',
            (tid, now(), kind, encode(detail)))

    def task(self, tid):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        need(row is not None, '任务不存在: ' + tid)
        result = dict(row)
        for key in ('source', 'contract', 'checkpoint', 'evidence'):
            if result[key]:
                result[key] = json.loads(result[key])
        return result

    def ingest(self, path):
        path = Path(path).resolve()
        need(path.is_file() and path.stat().st_size <= 2_000_000, '来源不存在或超过2MB')
        if path.suffix.lower() == '.json':
            proc = subprocess.run([node_bin(), str(ROOT/'scripts/kickoff-import.mjs'), str(path)],
                capture_output=True, text=True, timeout=20)
            need(proc.returncode == 0, proc.stderr.strip() or '开项导入失败')
            source = json.loads(proc.stdout)
        elif path.suffix.lower() in ('.md', '.txt'):
            source = {'name':path.stem, 'brief':path.read_text(encoding='utf-8'),
                'import_only':True, 'development_authorized':False, 'manual_review_required':True}
        else:
            raise ValueError('仅接受开项JSON、Markdown或纯文本简报')
        source['file_sha256'] = file_hash(path)
        source['import_path'] = str(path)
        # 同一内容从另一路径导入也只产生一项。
        key = source['file_sha256']
        with self.transaction():
            found = self.db.execute('SELECT id FROM tasks WHERE source_hash=?', (key,)).fetchone()
            if found:
                return self.task(found['id'])
            tid = 'T-' + key[:12]
            self.db.execute('INSERT INTO tasks(id,source_hash,source,status,created,updated) VALUES(?,?,?,?,?,?)',
                (tid, key, encode(source), 'DRAFT', now(), now()))
            self.event(tid, 'IMPORTED_AS_DRAFT', {'path':str(path)})
        return self.task(tid)

    def approve(self, tid, contract):
        route = validate_contract(contract)
        repo = str(Path(contract['repository']).resolve())
        need(Path(repo).is_dir(), '任务代码目录不存在')
        # Git emits '/' on Windows; compare the actual directory identity so
        # equivalent spelling is accepted without admitting a different root.
        need(Path(git(repo, 'rev-parse', '--show-toplevel')).samefile(repo), 'repository必须为Git工作树根目录')
        # 同一仓库的不同worktree也共用锁，避免两个任务改同一产品。
        common = Path(git(repo, 'rev-parse', '--git-common-dir'))
        repo_key = str((Path(repo)/common).resolve()) if not common.is_absolute() else str(common.resolve())
        contract = dict(contract, repository=repo, repository_key=repo_key, route=route)
        with self.transaction():
            t = self.task(tid)
            need(t['status'] == 'DRAFT', '只可批准草稿；已批准范围不能静默覆盖')
            source = t['source']
            unresolved = {x['id'] for x in source.get('handoff', {}).get('open', [])}
            unresolved |= {x['id'] for x in source.get('missing', [])}
            if source.get('issues'):
                unresolved.add('import-normalization')
            if source.get('manual_review_required'):
                unresolved.add('manual-brief-review')
            if source.get('handoff', {}).get('ai'):
                unresolved.add('delegated-choices')
            resolved = {x['id'] for x in contract['source_resolutions']}
            need(unresolved <= resolved, '未解决来源中的待定项: ' + ', '.join(sorted(unresolved-resolved)))
            self.db.execute('UPDATE tasks SET status=?,contract=?,contract_hash=?,repository=?,updated=? WHERE id=?',
                ('READY', encode(contract), digest(contract), repo_key, now(), tid))
            self.reviews.create(tid, contract)
            self.resources.create(tid, contract)
            self.event(tid, 'APPROVED', {'approval_ref':contract['approval_ref'], 'contract_hash':digest(contract)})
        return self.task(tid)

    def scan(self):
        inbox = self.root/'inbox'
        inbox.mkdir(exist_ok=True)
        errors = []
        for path in sorted(inbox.iterdir()):
            if path.is_file() and path.suffix.lower() in ('.json','.md','.txt'):
                try:
                    self.ingest(path)
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    errors.append({'file':path.name, 'error':str(exc)})
        rows = [dict(r) for r in self.db.execute('SELECT id,status,owner,updated,reason FROM tasks ORDER BY created')]
        # 重复心跳无需重复发同一份通知。
        last_event=self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        signature = digest({'tasks':rows,'errors':errors,'last_event':last_event})
        old = self.root/'last-scan.json'
        changed = not old.exists() or read_json(old).get('signature') != signature
        old.write_text(encode({'signature':signature}), encoding='utf-8')
        actions=[]
        for row in rows:
            if row['status'] in ('READY','RUNNING','REVIEW','BLOCKED','QUALITY_HOLD'):
                try:
                    plan=self.reviews.plan(row['id'])
                    actions.append({k:plan[k] for k in ('task_id','next_action','blockers','revision')})
                except ValueError as exc:
                    actions.append({'task_id':row['id'],'next_action':'ADOPT_REVIEW','reason':str(exc)})
        resource_actions=[]
        for row in rows:
            if row['status'] in ('READY','RUNNING','REVIEW','BLOCKED'):
                if self.resources.exists(row['id']):
                    rp=self.resources.plan(row['id'])
                    resource_actions.append({k:rp[k] for k in ('task_id','next_action','blockers','units','parallel_cap','usage')})
                else:resource_actions.append({'task_id':row['id'],'next_action':'ADOPT_RESOURCES'})
        return {'changed':changed, 'tasks':rows, 'errors':errors, 'review_actions':actions,'resource_actions':resource_actions,
                'business_actions':self.business.board(),
                'ready':[r['id'] for r in rows if r['status']=='READY'],
                'active':[r['id'] for r in rows if r['status']=='RUNNING']}

    def claim(self, tid, owner):
        with self.transaction():
            t = self.task(tid)
            need(t['status'] == 'READY', '任务未就绪或已被领取；不得重复启动')
            self.business.guard_task(tid)
            need(owner.strip(), '需要实际执行会话标识')
            self.reviews.can_claim(tid, owner)
            self.db.execute('UPDATE tasks SET status=?,owner=?,updated=? WHERE id=?',
                ('RUNNING', owner, now(), tid))
            self.event(tid, 'CLAIMED', {'owner':owner})
        return self.task(tid)

    def check_owner(self, t, owner):
        need(t['owner'] == owner, '只有登记的执行者可以更新任务')

    def checkpoint(self, tid, owner, path):
        ref = artifact(path)
        with self.transaction():
            t = self.task(tid)
            self.check_owner(t, owner)
            need(t['status'] in ('RUNNING', 'REVIEW', 'BLOCKED'), '任务不在执行中')
            self.db.execute('UPDATE tasks SET checkpoint=?,updated=? WHERE id=?', (encode(ref),now(),tid))
            self.event(tid, 'CHECKPOINT', ref)
        return self.task(tid)

    def block(self, tid, owner, reason):
        need(bool(reason.strip()), '阻塞原因不能为空')
        with self.transaction():
            t = self.task(tid)
            self.check_owner(t, owner)
            need(t['status'] in ('RUNNING','REVIEW'), '只有执行或复核任务能记录阻塞')
            self.db.execute('UPDATE tasks SET status=?,reason=?,updated=? WHERE id=?', ('BLOCKED',reason,now(),tid))
            self.event(tid,'BLOCKED',{'reason':reason})
        return self.task(tid)

    def resume(self, tid, owner, resolution):
        ref = artifact(resolution)
        with self.transaction():
            t = self.task(tid)
            self.check_owner(t, owner)
            need(t['status'] == 'BLOCKED', '只能恢复已阻塞任务')
            self.db.execute('UPDATE tasks SET status=?,reason=NULL,updated=? WHERE id=?', ('RUNNING',now(),tid))
            self.event(tid,'RESUMED',ref)
        return self.task(tid)

    def check_report(self, t, report):
        self.business.guard_task(t['id'])
        c = t['contract']
        self.reviews.enforce(t['id'])
        self.resources.enforce(t['id'])
        need(report.get('contract_hash') == t['contract_hash'], '验收绑定的需求版本不匹配')
        need(report.get('commit') == frozen_commit(c['repository']), '验收绑定的代码版本不匹配')
        need(report.get('environment') == 'local', '本地交付包不能声明生产通过')
        need(report.get('open_p0_p1') == [], '仍有未关闭P0/P1或未明确列出')
        required = {ac['id']:ac for ac in c['acceptance']}
        checks = report.get('checks', [])
        need(len(checks)==len(required) and {x.get('id') for x in checks}==set(required), '验收项缺失或重复')
        refs = []
        for result in checks:
            need(result.get('status') == 'PASS', '未执行、阻塞、失败不能写成交付通过')
            need(result.get('environment') == required[result['id']]['environment'], '验收环境与约定不符')
            need(result.get('observed'), '缺少实际观察结果')
            refs.append(artifact(result['evidence']))
        if c['ux_required']:
            need(report.get('ux',{}).get('kind') in ('browser_interaction','native_render_interaction'), '界面任务需要真实渲染和操作证据')
            refs.append(artifact(report['ux']['evidence']))
        review = report.get('review', {})
        if c['route']['task_mode'] != 'QUICK_PATCH':
            need(review.get('reviewer') and review['reviewer'] != t['owner'], '本任务需不同执行者/会话的复核')
            need(review.get('result') == 'PASS', '独立复核未通过')
            refs.append(artifact(review['evidence']))
        for path in report.get('artifacts', []):
            refs.append(artifact(path))
        need(report.get('artifacts'), '缺少可交付文件')
        return refs

    def submit(self, tid, owner, path):
        report = read_json(path)
        with self.transaction():
            t = self.task(tid)
            self.check_owner(t, owner)
            need(t['status']=='RUNNING', '任务不在执行中')
            refs = self.check_report(t, report)
            saved = {'report':report, 'files':refs, 'source':artifact(path)}
            self.db.execute('UPDATE tasks SET status=?,evidence=?,updated=? WHERE id=?', ('REVIEW',encode(saved),now(),tid))
            self.event(tid,'SUBMITTED',{'commit':report['commit']})
        return self.task(tid)

    def finish(self, tid, owner):
        with self.transaction():
            t = self.task(tid)
            self.check_owner(t, owner)
            need(t['status']=='REVIEW', '尚未提交可核对的交付包')
            evidence=t['evidence']
            for ref in evidence['files'] + [evidence['source']]:
                need(artifact(ref['path']) == ref, '证据提交后变化，须重新复核')
            self.check_report(t, evidence['report'])
            self.db.execute('UPDATE tasks SET status=?,updated=? WHERE id=?', ('DELIVERED',now(),tid))
            self.event(tid,'LOCAL_DELIVERED',{'not_production':True,'not_customer_acceptance':True})
            self.business.note_delivered(tid)
        return self.task(tid)

    def cancel(self, tid, reason):
        need(reason.strip(), '需记录用户取消/替换范围的来源及原因')
        with self.transaction():
            t=self.task(tid)
            need(not any(r['status'] in ('RESERVED','RUNNING') for r in self.resources.rows(tid)), '子Agent尚未结束，不能释放仓库锁')
            review=self.db.execute('SELECT state FROM review_state WHERE task_id=?',(tid,)).fetchone()
            need(not review or not json.loads(review[0]).get('active_run'),'实际检查尚未结束，不能释放仓库锁')
            need(t['status']!='DELIVERED', '已交付任务保留历史，另建变更任务')
            self.db.execute('UPDATE tasks SET status=?,reason=?,updated=? WHERE id=?', ('CANCELLED',reason,now(),tid))
            self.event(tid,'CANCELLED',{'reason':reason,'stop_writer_before_release':True})
            self.business.execution_stopped(tid,reason)
        return self.task(tid)

    def learn(self, tid, path):
        self.task(tid)
        data=read_json(path)
        for key in ('observation','candidate_rule','applies_when','does_not_apply_when','source_ref','next_validation'):
            need(isinstance(data.get(key),str) and data[key].strip(), '经验缺字段: '+key)
        data['evidence']=artifact(data['evidence_file'])
        data['status']='CANDIDATE'
        data['changes_permissions']=False
        key='L-'+digest({'task':tid,'proposal':data})[:12]
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO lessons VALUES (?,?,?,?)',(key,tid,now(),encode(data)))
        return {'id':key,**data}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime', default=str(ROOT/'runtime'))
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('scan')
    q=sub.add_parser('import');q.add_argument('file')
    for name in ('show','approve','claim','checkpoint','block','resume','submit','finish','cancel','learn'):
        q=sub.add_parser(name);q.add_argument('id')
        if name=='approve':q.add_argument('contract')
        if name in ('claim','checkpoint','block','resume','submit','finish'):q.add_argument('--owner',required=True)
        if name in ('checkpoint','resume','submit','learn'):q.add_argument('file')
        if name in ('block','cancel'):q.add_argument('reason')
    sub.add_parser('lessons')
    sub.add_parser('business-board')
    for name in ('business-register','business-receive'):
        q=sub.add_parser(name);q.add_argument('file')
    for name in ('business-show','business-outbox','business-assess','business-handoff','business-sync','business-revise','business-revoke'):
        q=sub.add_parser(name);q.add_argument('id')
        if name=='business-assess':q.add_argument('file')
        if name=='business-revise':q.add_argument('--actor',required=True)
        if name=='business-revoke':q.add_argument('reason')
    q=sub.add_parser('resource-quota');q.add_argument('file')
    for name in ('resource-plan','resource-runs','resource-observe','resource-wait','resource-decide','resource-configure','resource-run','resource-feedback','resource-integrated','resource-reconcile','resource-native-review','adopt-resources'):
        q=sub.add_parser(name);q.add_argument('id')
        if name not in ('resource-plan','resource-runs','resource-observe','resource-wait'):q.add_argument('--actor',required=True)
        if name in ('resource-configure','resource-feedback','resource-integrated','resource-reconcile','resource-decide','resource-native-review'):q.add_argument('file')
        if name=='resource-wait':q.add_argument('--seconds',type=int,default=30)
        if name=='resource-run':q.add_argument('--phase',choices=('work','review'),required=True)
    for name in ('review-plan','review-packet','review-refresh','adopt-review','review-assess','review-bind','review-run','review-finding','review-record','review-reconcile'):
        q=sub.add_parser(name);q.add_argument('id')
        if name not in ('review-plan','review-packet','review-refresh'):q.add_argument('--actor',required=True)
        if name in ('review-assess','review-bind','review-finding','review-record','review-reconcile'):q.add_argument('file')
        if name=='review-run':q.add_argument('--check')
        if name=='adopt-review':q.add_argument('--baseline',required=True)
    args=p.parse_args()
    queue=Queue(args.runtime)
    try:
        if args.cmd=='business-register':result=queue.business.register(read_json(args.file))
        elif args.cmd=='business-receive':result=queue.business.receive(read_json(args.file))
        elif args.cmd=='business-board':result=queue.business.board()
        elif args.cmd=='business-show':result=queue.business.view(args.id)
        elif args.cmd=='business-outbox':result=queue.business.outbox(args.id)
        elif args.cmd=='business-assess':result=queue.business.assess(args.id,read_json(args.file))
        elif args.cmd=='business-handoff':result=queue.business.handoff(args.id)
        elif args.cmd=='business-sync':result=queue.business.sync(args.id)
        elif args.cmd=='business-revise':result=queue.business.revise(args.id,args.actor)
        elif args.cmd=='business-revoke':result=queue.business.revoke(args.id,args.reason)
        elif args.cmd=='resource-plan':result=queue.resources.plan(args.id)
        elif args.cmd=='resource-runs':result=queue.resources.rows(args.id)
        elif args.cmd=='resource-observe':result=queue.resources.observe(args.id,record=True)
        elif args.cmd=='resource-wait':result=queue.resources.wait(args.id,args.seconds)
        elif args.cmd=='resource-decide':result=queue.resources.decide(args.id,args.actor,read_json(args.file))
        elif args.cmd=='resource-quota':result=queue.resources.set_quota(read_json(args.file))
        elif args.cmd=='resource-configure':result=queue.resources.configure(args.id,args.actor,read_json(args.file))
        elif args.cmd=='resource-run':result=queue.resources.run(args.id,args.actor,args.phase)
        elif args.cmd=='resource-native-review':result=queue.resources.native_review(args.id,args.actor,read_json(args.file))
        elif args.cmd=='resource-feedback':result=queue.resources.feedback(args.id,args.actor,read_json(args.file))
        elif args.cmd=='resource-integrated':result=queue.resources.integrated(args.id,args.actor,read_json(args.file))
        elif args.cmd=='resource-reconcile':result=queue.resources.reconcile(args.id,args.actor,args.file)
        elif args.cmd=='adopt-resources':result=queue.resources.adopt(args.id,args.actor)
        elif args.cmd=='review-plan':result=queue.reviews.plan(args.id)
        elif args.cmd=='review-packet':result=queue.reviews.packet(args.id)
        elif args.cmd=='review-refresh':result=queue.reviews.refresh(args.id)
        elif args.cmd=='adopt-review':result=queue.reviews.adopt(args.id,args.actor,args.baseline)
        elif args.cmd=='review-assess':result=queue.reviews.assess(args.id,args.actor,read_json(args.file))
        elif args.cmd=='review-bind':result=queue.reviews.bind(args.id,args.actor,read_json(args.file))
        elif args.cmd=='review-run':result=queue.reviews.run(args.id,args.actor,args.check)
        elif args.cmd=='review-finding':result=queue.reviews.finding(args.id,args.actor,read_json(args.file))
        elif args.cmd=='review-record':result=queue.reviews.record(args.id,args.actor,read_json(args.file))
        elif args.cmd=='review-reconcile':result=queue.reviews.reconcile(args.id,args.actor,args.file)
        elif args.cmd=='scan': result=queue.scan()
        elif args.cmd=='lessons':result=[dict(r) for r in queue.db.execute('SELECT * FROM lessons ORDER BY at')]
        elif args.cmd=='import': result=queue.ingest(args.file)
        elif args.cmd=='show': result=queue.task(args.id)
        elif args.cmd=='approve': result=queue.approve(args.id,read_json(args.contract))
        elif args.cmd=='claim': result=queue.claim(args.id,args.owner)
        elif args.cmd=='checkpoint': result=queue.checkpoint(args.id,args.owner,args.file)
        elif args.cmd=='block': result=queue.block(args.id,args.owner,args.reason)
        elif args.cmd=='resume': result=queue.resume(args.id,args.owner,args.file)
        elif args.cmd=='submit': result=queue.submit(args.id,args.owner,args.file)
        elif args.cmd=='cancel':result=queue.cancel(args.id,args.reason)
        elif args.cmd=='learn':result=queue.learn(args.id,args.file)
        else:result=queue.finish(args.id,args.owner)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,sqlite3.Error,subprocess.SubprocessError,KeyError,TypeError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2
    finally:
        queue.db.close()
    return 0

if __name__=='__main__':
    sys.exit(main())
