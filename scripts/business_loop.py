"""Event-driven request-to-acceptance loop; local inbox/outbox, no outbound transport.

Project mandates are installed by the trusted operator. Customer events never
create mandates, prices, schedules or production authorization.
"""
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess

from review_engine import require, proof, valid_proof, digest, dumped, clean_commit, git
from task_mode import normalize_signals

FIELDS = {'goal':'希望解决什么问题？', 'target':'具体是哪个页面或功能？', 'expected':'调整后希望看到什么结果？'}
TERMINAL = {'CLOSED','WITHDRAWN'}


class Business:
    def __init__(self, q):
        self.q=q
        q.db.executescript('''
          CREATE TABLE IF NOT EXISTS business_projects(id TEXT PRIMARY KEY, config TEXT NOT NULL, active INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS business_cases(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS business_messages(project_id TEXT NOT NULL, event_id TEXT NOT NULL,
            case_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id,event_id));
          CREATE TABLE IF NOT EXISTS business_outbox(id TEXT PRIMARY KEY, case_id TEXT NOT NULL,
            dedupe_key TEXT NOT NULL, message TEXT NOT NULL, UNIQUE(case_id,dedupe_key));
          CREATE TABLE IF NOT EXISTS business_tasks(task_id TEXT PRIMARY KEY, case_id TEXT NOT NULL);
        ''')
        q.db.commit()

    def project(self, pid, allow_revoked=False):
        row=self.q.db.execute('SELECT config,active FROM business_projects WHERE id=?',(pid,)).fetchone()
        require(row is not None,'PROJECT_NOT_REGISTERED: 需要已有项目授权，不能把客户消息当经营授权')
        p=json.loads(row['config'])
        require(allow_revoked or (row['active'] and valid_proof(p['authorization_evidence'])),'PROJECT_AUTHORIZATION_INACTIVE')
        return p

    def register(self, data):
        require(data.get('id') and re.fullmatch(r'[a-zA-Z0-9_-]{1,50}',data['id']),'项目ID无效')
        for key in ('authorization_ref','owner','repository'):
            require(isinstance(data.get(key),str) and data[key].strip(),'缺少项目授权字段:'+key)
        require(data.get('autonomy')=='local_delivery_preapproved' and data.get('exclusive_repository') is True,
                '仅支持预授权本地交付及明确专用的自动化工作树')
        require(data.get('external_send') is False,'此版本没有外部联系渠道')
        customers=data.get('customer_ids',[])
        require(isinstance(customers,list) and customers and all(isinstance(x,str) and x for x in customers),'需列明本项目客户标识')
        services=data.get('services',{})
        require(isinstance(services,dict) and services,'需已授权服务范围')
        repo=str(Path(data['repository']).resolve())
        try:
            git_root = Path(git(repo, 'rev-parse', '--show-toplevel')).resolve()
            repo_root = Path(repo).resolve()
            same_root = git_root.samefile(repo_root)
        except (OSError, ValueError, subprocess.SubprocessError):
            same_root = False
        require(same_root, 'repository需Git工作树根目录')
        for sid,s in services.items():
            require(re.fullmatch(r'[a-zA-Z0-9_-]{1,50}',sid) and s.get('label') and s.get('excluded'),'服务需标识、名称及排除项')
            require(type(s.get('ux_required')) is bool and s.get('environment'),'服务需真实验收环境')
            require(isinstance(s.get('risk_signals'),list) and s['risk_signals'],'服务需预定义风险信号')
            normalize_signals(s['risk_signals'])
            paths=s.get('paths',[])
            require(isinstance(paths,list) and paths,'服务需有代码边界')
            for path in paths:
                require(isinstance(path,str) and path and not PurePosixPath(path).is_absolute() and
                        '..' not in PurePosixPath(path).parts and '\\' not in path,'服务路径无效')
                require((Path(repo)/path).resolve().is_relative_to(Path(repo)),'服务路径逃逸工作树')
        config={**data,'repository':repo,'authorization_evidence':proof(data['authorization_evidence'])}
        with self.q.transaction():
            old=self.q.db.execute('SELECT config,active FROM business_projects WHERE id=?',(data['id'],)).fetchone()
            require(not old or json.loads(old[0])==config,'项目授权不可静默覆盖；新范围使用新的授权版本ID')
            if old:return {'project_id':data['id'],'status':'REGISTERED' if old['active'] else 'REVOKED','external_send':False}
            self.q.db.execute('INSERT OR IGNORE INTO business_projects VALUES (?,?,1)',(data['id'],dumped(config)))
            self.q.event(None,'BUSINESS_MANDATE_REGISTERED',{'project':data['id'],'authorization_ref':data['authorization_ref']})
        return {'project_id':data['id'],'status':'REGISTERED','external_send':False}

    def revoke(self, pid, reason):
        require(reason.strip(),'撤销需记录原因')
        with self.q.transaction():
            require(self.q.db.execute('SELECT 1 FROM business_projects WHERE id=?',(pid,)).fetchone(),'未知项目')
            self.q.db.execute('UPDATE business_projects SET active=0 WHERE id=?',(pid,))
            self.q.event(None,'BUSINESS_MANDATE_REVOKED',{'project':pid,'reason':reason})
        return {'project_id':pid,'status':'REVOKED'}

    def load(self, cid):
        row=self.q.db.execute('SELECT state FROM business_cases WHERE id=?',(cid,)).fetchone()
        require(row is not None,'未知需求单')
        return json.loads(row[0])

    def save(self, s):
        self.q.db.execute('INSERT OR REPLACE INTO business_cases VALUES (?,?,?)',(s['id'],s['project_id'],dumped(s)))

    def emit(self, s, key, kind, body, **extra):
        oid='O-'+digest({'case':s['id'],'key':key})[:18]
        message={'id':oid,'kind':kind,'body':body,'customer_id':s['customer_id'],'conversation_id':s['conversation_id'],
                 'channel':'local_outbox','transport_status':'NOT_EXTERNALLY_SENT',**extra}
        self.q.db.execute('INSERT OR IGNORE INTO business_outbox VALUES (?,?,?,?)',(oid,s['id'],key,dumped(message)))
        return oid

    def outbox(self, cid):
        self.load(cid)
        return [json.loads(r[0]) for r in self.q.db.execute('SELECT message FROM business_outbox WHERE case_id=? ORDER BY rowid',(cid,))]

    def view(self, cid):
        s=self.load(cid)
        try:self.project(s['project_id']); authorized=True
        except ValueError:authorized=False
        action={'NEEDS_ASSESSMENT':'ASSESS_CUSTOMER_MESSAGE','WAITING_INFORMATION':'WAIT_CUSTOMER_REPLY',
                'AWAITING_CONFIRMATION':'WAIT_CUSTOMER_SCOPE_CONFIRMATION','CONFIRMED':'HANDOFF',
                'DEVELOPMENT':'CONTINUE_DELIVERY_TASK','AWAITING_ACCEPTANCE':'WAIT_CUSTOMER_ACCEPTANCE',
                'CHANGE_PENDING':'PRESERVE_AND_REVISE_SCOPE','WITHDRAW_PENDING':'STOP_EXECUTION_AND_WITHDRAW',
                'EXECUTION_STOPPED':'RESOLVE_STOPPED_EXECUTION','DELIVERY_ISSUE':'INVESTIGATE_DELIVERY',
                'POLICY_EXCEPTION':'RESOLVE_POLICY_EXCEPTION','CLOSED':'DONE','WITHDRAWN':'STOP'}[s['status']]
        if s.get('waiting_task_id') and self.q.task(s['waiting_task_id'])['status'] in ('RUNNING','REVIEW','BLOCKED'):
            action='WAIT_REPOSITORY'
        return {**s,'next_action':action if authorized else 'PROJECT_AUTHORIZATION_INACTIVE',
                'outbox':self.outbox(cid),'authorization_active':authorized}

    def board(self):
        result=[]
        for row in self.q.db.execute('SELECT id FROM business_cases ORDER BY rowid'):
            s=self.view(row[0]);result.append({k:s.get(k) for k in ('id','project_id','status','revision','task_id','next_action')})
        return result

    def receive(self, event):
        for key in ('project_id','event_id','customer_id','conversation_id','kind','text'):
            require(isinstance(event.get(key),str) and event[key].strip(),'客户事件缺少:'+key)
        require(len(event['text'])<=16000,'客户消息过长，请使用有边界的需求材料')
        require(event['kind'] in ('request','reply','confirm','change','accept','reject','withdraw'),'未知客户事件类型')
        with self.q.transaction():
            old=self.q.db.execute('SELECT case_id,payload FROM business_messages WHERE project_id=? AND event_id=?',
                                 (event['project_id'],event['event_id'])).fetchone()
            if old:
                require(json.loads(old['payload'])==event,'EVENT_ID_COLLISION: 相同事件ID内容发生变化')
                return self.view(old['case_id'])
            p=self.project(event['project_id'],allow_revoked=event['kind']=='withdraw')
            require(event['customer_id'] in p['customer_ids'],'客户不属于该项目')
            cid='B-'+digest({'project':event['project_id'],'customer':event['customer_id'],'conversation':event['conversation_id']})[:18]
            row=self.q.db.execute('SELECT state FROM business_cases WHERE id=?',(cid,)).fetchone()
            if row:s=json.loads(row[0])
            else:
                require(event['kind']=='request','新需求必须从request开始')
                s={'id':cid,'project_id':p['id'],'policy_hash':digest(p),'customer_id':event['customer_id'],
                   'conversation_id':event['conversation_id'],'revision':0,'status':'NEEDS_ASSESSMENT',
                   'messages':[],'facts':{},'proposal':None,'confirmed_proposal':None,'task_id':None,
                   'history':[],'delivery':None,'question_rounds':0}
            require(s['status'] not in TERMINAL,'已结案；新需求使用新的conversation_id')
            kind=event['kind']
            if kind in ('request','reply','change'):
                require(kind!='request' or not s['messages'],'已有会话请发送reply/change，避免重复开项')
                require(kind=='change' or s['status'] not in ('DEVELOPMENT','AWAITING_ACCEPTANCE','DELIVERY_ISSUE','CHANGE_PENDING','EXECUTION_STOPPED'),
                        '开发后新增要求需明确change，不能静默改验收')
                require(s['status']!='WITHDRAW_PENDING','先处理已请求的撤回')
                s['revision']+=1;s['proposal']=None;s['confirmed_proposal']=None;s['facts']={}
                if kind=='change':s['evidence_start']=len(s['messages'])
                s['status']='CHANGE_PENDING' if s['task_id'] else 'NEEDS_ASSESSMENT'
            elif kind=='confirm':
                require(s['status']=='AWAITING_CONFIRMATION' and event.get('proposal_id')==s['proposal']['id'],
                        'STALE_CONFIRMATION: 必须确认当前完整范围版本')
                s['confirmed_proposal']=s['proposal']['id'];s['status']='CONFIRMED'
            elif kind=='accept':
                require(s['status']=='AWAITING_ACCEPTANCE' and event.get('delivery_id')==s['delivery']['id'],
                        'STALE_ACCEPTANCE: 必须验收当前交付版本')
                self.verify_delivery(s)
                s['status']='CLOSED';s['accepted_event']=event['event_id']
                self.emit(s,'closed:'+s['delivery']['id'],'closed','本次需求已按你的确认完成验收，需求与交付记录已归档。')
            elif kind=='reject':
                require(s['status']=='AWAITING_ACCEPTANCE' and event.get('delivery_id')==s['delivery']['id'],'拒收需对应当前交付')
                s['status']='DELIVERY_ISSUE';s['rejection']=event['text']
                self.emit(s,'rejection:'+event['event_id'],'acknowledgment','已记录验收未通过的具体问题，将核对原验收标准并处理，不把本次标为已验收。')
            elif kind=='withdraw':
                if s['task_id']:
                    s['status']='WITHDRAW_PENDING';self.emit(s,'withdraw-request','acknowledgment','已收到撤回要求，正在处理已开始工作的停止和保留。')
                else:
                    s['status']='WITHDRAWN';self.emit(s,'withdraw','closed','本次需求已撤回，未安排开发。')
            s['messages'].append(event)
            self.q.db.execute('INSERT INTO business_messages VALUES (?,?,?,?)',(p['id'],event['event_id'],cid,dumped(event)))
            self.save(s);self.q.event(s['task_id'],'BUSINESS_CUSTOMER_EVENT',{'case':cid,'event_id':event['event_id'],'kind':kind})
        if s['status']=='CONFIRMED':return self.handoff(cid)
        return self.view(cid)

    def assess(self, cid, data):
        with self.q.transaction():
            s=self.load(cid);p=self.project(s['project_id'])
            require(s['status'] in ('NEEDS_ASSESSMENT','WAITING_INFORMATION','AWAITING_CONFIRMATION','POLICY_EXCEPTION'),'当前状态不能改需求解释')
            require(data.get('revision')==s['revision'],'需求消息版本已过期')
            require(data.get('reasoning') and isinstance(data.get('risks'),list),'需实际判断理由和风险，不只填表')
            facts=data.get('facts',{});require(isinstance(facts,dict) and set(facts)<=set(FIELDS),'事实字段无效')
            messages={m['event_id']:m for m in s['messages'] if m['kind'] in ('request','reply','change','reject')}
            for key,f in facts.items():
                require(isinstance(f,dict) and all(isinstance(f.get(k),str) and f[k].strip() for k in ('value','event_id','quote')),'事实必须带客户原文来源')
                require(f['event_id'] in messages and f['quote'] in messages[f['event_id']]['text'],'事实引用不是本会话客户原文')
            if s.get('evidence_start'):
                recent={m['event_id'] for m in s['messages'][s['evidence_start']: ]}
                require('expected' not in facts or facts['expected']['event_id'] in recent,
                        '变更后的验收解释必须引用本轮新要求；未变的目标与位置可保留')
            sid=data.get('service_id');missing=[key for key in FIELDS if key not in facts]
            s['facts']=facts;s['assessment']={'reasoning':data['reasoning'],'risks':data['risks'],'service_id':sid}
            if sid not in p['services'] or data['risks']:
                s['status']='POLICY_EXCEPTION';s['exception']='需求超出已授权服务，或存在未解决风险'
                self.emit(s,'exception:'+str(s['revision']),'clarification','这项需求需要进一步核对适用范围，暂未承诺新增费用、工期或实施。',reason=s['exception'])
            elif missing:
                s['status']='WAITING_INFORMATION';key='questions:'+str(s['revision'])
                if not self.q.db.execute('SELECT 1 FROM business_outbox WHERE case_id=? AND dedupe_key=?',(cid,key)).fetchone():s['question_rounds']+=1
                if s['question_rounds']>3:
                    s['status']='POLICY_EXCEPTION';s['exception']='补问三轮仍缺关键事实，需针对歧义重新判断'
                else:self.emit(s,key,'questions','为准确处理，请补充：\n'+'\n'.join(FIELDS[k] for k in missing),missing_fields=missing)
            else:
                service=p['services'][sid]
                proposal={'revision':s['revision'],'service_id':sid,'goal':facts['goal']['value'],'target':facts['target']['value'],
                          'acceptance':facts['expected']['value'],'excluded':service['excluded'],'delivery_environment':service['environment'],
                          'commitment':'仅沿用现有项目授权；本确认单不新增报价、收费、工期或上线承诺。'}
                proposal['id']='P-'+digest({'case':cid,'policy':s['policy_hash'],'proposal':proposal})[:20]
                s['proposal']=proposal;s['status']='AWAITING_CONFIRMATION'
                body='请确认本次范围：\n目标：'+proposal['goal']+'\n位置：'+proposal['target']+'\n验收：'+proposal['acceptance']+'\n不含：'+'、'.join(proposal['excluded'])+'\n'+proposal['commitment']
                self.emit(s,'proposal:'+proposal['id'],'scope_confirmation',body,proposal_id=proposal['id'])
            self.save(s);self.q.event(None,'BUSINESS_ASSESSED',{'case':cid,'revision':s['revision'],'status':s['status']})
        return self.view(cid)

    def handoff(self, cid):
        with self.q.transaction():
            s=self.load(cid);p=self.project(s['project_id'])
            if s['task_id']:return self.view(cid)
            require(s['status']=='CONFIRMED' and s['confirmed_proposal']==s['proposal']['id'],'客户尚未确认当前版本')
            require(s['policy_hash']==digest(p),'项目授权版本变化')
            service=p['services'][s['proposal']['service_id']]
            clean_commit(p['repository'])
            common=Path(git(p['repository'],'rev-parse','--git-common-dir'))
            repo_key=str((Path(p['repository'])/common).resolve()) if not common.is_absolute() else str(common.resolve())
            busy=self.q.db.execute("SELECT id FROM tasks WHERE repository=? AND status IN ('RUNNING','REVIEW','BLOCKED')",(repo_key,)).fetchone()
            if busy:
                s['waiting_task_id']=busy['id'];self.save(s);return self.view(cid)
            directory=self.q.root/'business-artifacts'/cid;directory.mkdir(parents=True,exist_ok=True)
            brief=directory/(s['proposal']['id']+'.md');brief.write_text(dumped({'case':cid,'proposal':s['proposal'],'policy_ref':p['authorization_ref']}), encoding='utf-8')
            task=self.q.ingest(brief)
            c={'goal':s['proposal']['goal'],'repository':p['repository'],'owner':p['owner'],'approval_ref':p['authorization_ref'],
               'autonomy':'local_until_delivery','rollback':'保留当前版本，以独立提交回退本次范围内的修改',
               'scope':[s['proposal']['target']+': '+s['proposal']['acceptance']],
               'excluded':service['excluded'],'acceptance':[{'id':'CUSTOMER-1','expected':s['proposal']['acceptance'],'environment':service['environment']}],
               'risk_signals':service['risk_signals'],'estimated_business_files':len(service['paths']),
               'source_resolutions':[{'id':'manual-brief-review','decision':'项目预授权与客户当前确认版本已核对','evidence':s['confirmed_proposal']}],
               'open_business_decisions':[],'repository_idle_confirmed':True,'ux_required':service['ux_required'],
               'review_components':[{'id':'service','paths':service['paths'],'depends_on':[],'risk_signals':service['risk_signals']}],
               'business_case':cid,'business_proposal':s['confirmed_proposal']}
            task=self.q.approve(task['id'],c)
            s['task_id']=task['id'];s['status']='DEVELOPMENT';s.pop('waiting_task_id',None)
            self.q.db.execute('INSERT INTO business_tasks VALUES (?,?)',(task['id'],cid))
            self.save(s);self.emit(s,'handoff:'+s['confirmed_proposal'],'accepted','已确认本次范围并安排实施，后续将按确认的验收标准核对结果。')
            self.q.event(task['id'],'BUSINESS_HANDOFF',{'case':cid,'proposal':s['confirmed_proposal']})
        return self.view(cid)

    def guard_task(self, tid):
        row=self.q.db.execute('SELECT case_id FROM business_tasks WHERE task_id=?',(tid,)).fetchone()
        if not row:return
        s=self.load(row[0]);p=self.project(s['project_id'])
        require(s['task_id']==tid and s['policy_hash']==digest(p),'BUSINESS_SCOPE_SUPERSEDED')
        require(s['status'] in ('DEVELOPMENT','AWAITING_ACCEPTANCE','CLOSED'),'BUSINESS_SCOPE_ON_HOLD: 变更或验收问题尚未解决')
        t=self.q.task(tid)
        require(t['contract'].get('business_proposal')==s['confirmed_proposal'],'BUSINESS_CONFIRMATION_CHANGED')

    def execution_stopped(self, tid, reason):
        row=self.q.db.execute('SELECT case_id FROM business_tasks WHERE task_id=?',(tid,)).fetchone()
        if not row:return
        s=self.load(row[0])
        if s['task_id']==tid and s['status']=='DEVELOPMENT':
            s['status']='EXECUTION_STOPPED';s['exception']=reason;self.save(s)
            self.emit(s,'stopped:'+tid,'execution_update','本次实施已停止推进，已有工作和需求记录保留，正在核对后续处理。')

    def quality_hold(self, tid, finding_id):
        row=self.q.db.execute('SELECT case_id FROM business_tasks WHERE task_id=?',(tid,)).fetchone()
        if not row:return
        s=self.load(row[0])
        if s['task_id']!=tid or s['status']=='WITHDRAW_PENDING':return
        if s['status']=='CLOSED':s['previous_acceptance_event']=s.get('accepted_event')
        s['status']='DELIVERY_ISSUE';s['exception']='交付后发现缺陷，需跟进修复；既有验收历史保留'
        self.save(s)
        self.emit(s,'quality-hold:'+tid+':'+finding_id,'quality_update','此前交付发现需要修正的问题，已进入后续处理；已有需求和验收记录保留。')

    def verify_delivery(self, s):
        t=self.q.task(s['task_id'])
        require(t['status']=='DELIVERED' and t['evidence'],'尚无实际通过审核的交付')
        self.guard_task(t['id'])
        require(s['delivery'] and s['delivery']['commit']==t['evidence']['report']['commit'],'缺少真实交付快照')
        for ref in s['delivery']['files']:require(valid_proof(ref),'交付归档证据已变化')
        require(s['delivery']['report_hash']==digest(t['evidence']['report']),'交付报告已变化')
        return t

    def note_delivered(self, tid):
        """Called inside Queue.finish after all actual gates pass; snapshot before later work."""
        row=self.q.db.execute('SELECT case_id FROM business_tasks WHERE task_id=?',(tid,)).fetchone()
        if not row:return
        s=self.load(row[0]);t=self.q.task(tid);self.guard_task(tid)
        require(t['status']=='DELIVERED' and t['evidence'],'只归档已通过实际交付门禁的任务')
        report=t['evidence']['report']
        did='D-'+digest({'task':tid,'commit':report['commit'],'report':report})[:20]
        directory=self.q.root/'business-artifacts'/s['id']/did;directory.mkdir(parents=True,exist_ok=True)
        files=[]
        for i,ref in enumerate(t['evidence']['files']+[t['evidence']['source']]):
            require(valid_proof(ref),'交付归档前证据已变化')
            dest=directory/(str(i)+'-'+Path(ref['path']).name)
            shutil.copyfile(ref['path'],dest)
            archived=proof(dest);require(archived['sha256']==ref['sha256'],'归档期间证据发生变化')
            files.append(archived)
        s['delivery']={'id':did,'task_id':tid,'commit':report['commit'],'files':files,'report_hash':digest(report)}
        s['status']='AWAITING_ACCEPTANCE';self.save(s)
        self.emit(s,'delivery:'+did,'acceptance_request','本次本地交付已完成检查，请按以下标准验收：\n'+s['proposal']['acceptance']+'\n请明确反馈通过或具体未通过之处。',delivery_id=did)
        self.q.event(tid,'BUSINESS_DELIVERY_OFFERED',{'case':s['id'],'delivery_id':did})

    def sync(self, cid):
        with self.q.transaction():
            s=self.load(cid)
            if s['status']=='DELIVERY_ISSUE':return self.view(cid)
            require(s['status'] in ('DEVELOPMENT','AWAITING_ACCEPTANCE'),'当前无需同步交付')
            t=self.q.task(s['task_id'])
            if t['status']!='DELIVERED':
                if t['status']=='QUALITY_HOLD':
                    s['status']='DELIVERY_ISSUE';s['exception']='交付质量结论已撤回';self.save(s)
                return self.view(cid)
            t=self.verify_delivery(s)
            did=s['delivery']['id'];s['status']='AWAITING_ACCEPTANCE'
            self.save(s)
            self.emit(s,'delivery:'+did,'acceptance_request','本次本地交付已完成检查，请按以下标准验收：\n'+s['proposal']['acceptance']+'\n请明确反馈通过或具体未通过之处。',delivery_id=did)
            self.q.event(t['id'],'BUSINESS_DELIVERY_OFFERED',{'case':cid,'delivery_id':did})
        return self.view(cid)

    def revise(self, cid, actor):
        with self.q.transaction():
            s=self.load(cid)
            require(s['status'] in ('CHANGE_PENDING','DELIVERY_ISSUE','WITHDRAW_PENDING','EXECUTION_STOPPED'),'没有待处理的变更或验收问题')
            withdraw=s['status']=='WITHDRAW_PENDING'
            if not withdraw:self.project(s['project_id'])
            t=self.q.task(s['task_id'])
            if t['owner']:self.q.check_owner(t,actor)
            require(not any(r['status'] in ('RESERVED','RUNNING') for r in self.q.resources.rows(t['id'])),'先停止实际子Agent')
            require(not self.q.reviews.load(t['id'])['active_run'],'先结束实际检查进程')
            clean_commit(t['contract']['repository'])
            if t['status'] not in ('DELIVERED','QUALITY_HOLD','CANCELLED'):self.q.cancel(t['id'],'客户变更/验收问题另立版本：'+cid)
            s['history'].append({'task_id':t['id'],'delivery':s['delivery'],'revision':s['revision']})
            s.update(task_id=None,delivery=None,proposal=None,confirmed_proposal=None,facts={},status='NEEDS_ASSESSMENT')
            if withdraw:
                s['status']='WITHDRAWN';self.emit(s,'withdraw','closed','本次需求已停止推进，已有工作和记录已保留。')
            s['revision']+=1;self.save(s)
            self.q.event(t['id'],'BUSINESS_REVISION_REQUIRED',{'case':cid,'revision':s['revision']})
        return self.view(cid)
