"""Run the existing control plane around a native Codex conversation.

Hooks supply observations; Codex keeps its own model, tools and context. This
adapter does not parse transcripts, start another model loop, or grant tools.
"""
from contextlib import contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid

from .intake import checked_path, fingerprint
from .memory import MemoryStore
from control_plane.runtime import DeliveryRuntime
from control_plane.evaluator import make_verifier


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def save(path,value):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(temporary,path)


def hint_path(session_id):
    return Path(tempfile.gettempdir())/'agi-like-native-homes'/(digest(session_id)+'.json')


def session_home(session_id):
    try:
        value=json.loads(hint_path(session_id).read_text(encoding='utf-8'))
        if value['session_id']==session_id and time.time()-value['created']<86400:return Path(value['home'])
    except (OSError,ValueError,KeyError,TypeError):pass
    return Path.home()/'.agi-like/native'


class NativeSession:
    def __init__(self,home):
        self.home=Path(home).expanduser().resolve();self.home.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS prompts(id TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS history(task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, value TEXT NOT NULL)')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.home/'sessions.sqlite3',timeout=45)
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    @staticmethod
    def load(db,session_id):
        row=db.execute('SELECT value FROM sessions WHERE id=?',(session_id,)).fetchone()
        if not row:return None
        value=json.loads(row[0]);value.setdefault('intents',{});value.setdefault('unmatched_intents',{})
        return value

    @staticmethod
    def store(db,session_id,value):
        db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?)',(session_id,json.dumps(value,ensure_ascii=False,allow_nan=False)))

    @staticmethod
    def control(task):return DeliveryRuntime(Path(task['root'])/'control.sqlite3',evidence_root=task['workspace'])

    def memory(self,workspace):
        key=hashlib.sha256(os.path.normcase(str(Path(workspace).resolve())).encode()).hexdigest()[:24]
        return MemoryStore(self.home.parent/'projects'/key/'memory.sqlite3',scope='workspace-'+key)

    def status(self,session_id):
        with self.db() as db:task=self.load(db,session_id)
        return task or {'status':'NOT_REGISTERED','session_id':session_id}

    def begin(self,session_id,workspace,inputs,outputs,review_script,*,goal=None,max_repairs=2,max_tools=100,max_seconds=3600):
        if not isinstance(session_id,str) or not session_id.strip():raise ValueError('Codex session id is required')
        if type(max_repairs) is not int or not 0<=max_repairs<=5:raise ValueError('max_repairs must be 0..5')
        if not 1<=max_tools<=1000 or not 1<=max_seconds<=86400:raise ValueError('invalid native tool/time budget')
        workspace=Path(workspace).resolve(strict=True)
        if not workspace.is_dir() or self.home.is_relative_to(workspace):raise ValueError('native state must stay outside task workspace')
        def relative_names(values):
            result=[]
            for value in values:
                path=Path(value)
                if path.is_absolute():
                    try:name=path.relative_to(workspace).as_posix()
                    except ValueError:raise ValueError('artifact is outside the declared workspace; keep the correct workspace and use paths inside it') from None
                else:name=path.as_posix()
                checked_path(workspace,name);result.append(name)
            return result
        inputs=relative_names(inputs);outputs=relative_names(outputs)
        if not outputs or len(inputs+outputs)>200 or len(set(inputs+outputs))!=len(inputs+outputs):raise ValueError('declare distinct inputs and outputs, at most 200')
        for name in outputs:checked_path(workspace,name)
        pinned=[fingerprint(workspace,name) for name in inputs]
        review=Path(review_script).resolve(strict=True)
        if review.stat().st_size>1024*1024:raise ValueError('reviewer must be at most 1 MiB')
        if review.is_relative_to(workspace) and review.relative_to(workspace).as_posix() not in inputs:
            raise ValueError('project reviewer must be a declared read-only input before it is frozen')
        review_bytes=review.read_bytes()
        with self.db() as db:
            original=db.execute('SELECT value FROM prompts WHERE id=?',(session_id,)).fetchone()
            original=json.loads(original[0]) if original else None
            if original and time.time()-original['created']<86400:
                if goal is not None and goal!=original['goal']:raise ValueError('goal differs from actual submitted Codex prompt')
                goal=original['goal']
            if not isinstance(goal,str) or not goal.strip() or len(goal)>65536:raise ValueError('original user goal is required')
            binding={'goal':goal,'workspace':str(workspace),'inputs':pinned,'outputs':outputs,
                     'review_sha256':hashlib.sha256(review_bytes).hexdigest()}
            prior=self.load(db,session_id)
            if prior and prior['status'] not in {'COMPLETED','CANCELLED'}:
                if prior['binding']==binding:return {**prior,'reused':True}
                raise ValueError('this session already has an unresolved task; continue or explicitly cancel it')
            task_id='native-'+uuid.uuid4().hex[:16];root=self.home/'tasks'/task_id;root.mkdir(parents=True)
            (root/'trusted-review.py').write_bytes(review_bytes)
            task={'format':'codex-native-runtime/1','task_id':task_id,'session_id':session_id,'root':str(root),
                  'workspace':str(workspace),'binding':binding,'status':'RUNNING','created':time.time(),
                  'repairs':0,'max_repairs':max_repairs,'tool_count':0,'max_tools':max_tools,'max_seconds':max_seconds,
                  'pending':{},'intents':{},'unmatched_intents':{},'hook_events':{},'reviews':[],'hook_observed':False,'user_messages':1,
                  'limits':'Native hooks are not an OS sandbox or complete tool-coverage guarantee; no automatic external-effect replay.'}
            rt=self.control(task)
            contract={'task_id':task_id,'goal':goal,'owner':'codex-native-executor','candidate':digest(binding),
                      'authorization_ref':'current-codex-request:'+session_id,'session_id':session_id,'scope':outputs,'inputs':inputs,
                      'excluded':['authority changes','unobserved external effects'],
                      'policy':{'workspace_write':True,'external_actions':False,'shell_required':False},
                      'budget':{'max_steps':max_tools+2,'max_retries':max(1,max_repairs),'max_seconds':max_seconds},
                      'required_review_sha256':binding['review_sha256'],
                      'acceptance':[{'id':'output-'+str(i),'expected':'Independently checked original-goal artifact '+name,
                          'environment':'local','assertion':{'type':'file_exists','path':name}} for i,name in enumerate(outputs)]}
            state=rt.create_task(contract,{'fs.read':True,'fs.write':True,'evidence.hash':True,'checkpoint.persist':True},adapter_id='codex-native-hooks',host_id='codex')
            task['lease_id']=rt.claim(task_id,'codex-native-executor',ttl_seconds=max_seconds+60)['lease_id']
            rt.apply(task_id,task['lease_id'],'start',{'executor':'codex-native-session'})
            save(root/'request.json',binding)
            self.store(db,session_id,task)
            memory=self.memory(workspace)
            return {**task,'relevant_experience':memory.search(goal,limit=3),'reused':False}

    def _receipt(self,task):
        rt=self.control(task)
        directory=Path(task['root'])/'receipts';directory.mkdir(exist_ok=True)
        path=directory/(uuid.uuid4().hex+'.json');task['last_receipt']=str(path)
        value={**task,'control_state':rt.get(task['task_id']),'control_events':rt.events(task['task_id'])}
        with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
        save(Path(task['root'])/'receipt.json',value)
        if task['status']=='COMPLETED' and not task.get('memory_recorded'):
            try:
                self.memory(task['workspace']).add(task['binding']['goal'],kind='success',task_id=task['task_id'],
                    data={'outputs':task['completed_packet']['outputs'],'repairs':task['repairs']},source_refs=[str(path)])
                task['memory_recorded']=True
            except (OSError,ValueError,sqlite3.Error):task['memory_recorded']=False
        return value

    def verify(self,session_id,*,at_stop=False):
        with self.db() as db:
            task=self.load(db,session_id)
            if not task:return {'status':'NOT_REGISTERED'}
            if task['status']=='RUNNING' and (task['pending'] or task.get('intents')) and not at_stop:
                task['verify_requested']=True;self.store(db,session_id,task)
                return {**task,'status':'VERIFICATION_QUEUED','reason':'The current native tool has not returned yet. Finish normally; Stop will verify after all tool results arrive. Do not poll verify in a loop.'}
            if at_stop and task.get('intents'):
                # Stop is the native conversation boundary, not evidence that
                # every Pre hook led to execution. Rejections/unsupported paths
                # may have no Post hook. Preserve that coverage gap; only known
                # running processes/interrupted calls belong in pending.
                task['unmatched_intents'].update(task['intents']);task['intents']={}
            task['tool_observation_coverage']='partial' if task.get('unmatched_intents') else 'observed-supported-calls'
            result=self._verify(task)
            self.store(db,session_id,task)
            return result

    def _verify(self,task):
        if task['status']=='COMPLETED':
            try:
                packet=task['completed_packet']
                for row in packet['inputs']+packet['outputs']:
                    if fingerprint(task['workspace'],row['path'])!=row:raise ValueError('completed candidate changed')
                if hashlib.sha256((Path(task['root'])/'trusted-review.py').read_bytes()).hexdigest()!=task['binding']['review_sha256']:raise ValueError('completed reviewer changed')
            except (ValueError,OSError,KeyError) as exc:
                task['status']='UNVERIFIED';task['reason']=str(exc)
            return self._receipt(task)
        if task['status']!='RUNNING':return self._receipt(task)
        root=Path(task['root']);workspace=Path(task['workspace']);binding=task['binding'];rt=self.control(task)
        if rt.get(task['task_id'])['status']=='COMPLETED':
            task['status']='NEEDS_RECONCILIATION';task['reason']='Control plane already committed completion but native index was not saved; inspect its receipt before continuing.'
            return self._receipt(task)
        if task['pending']:
            task['status']='NEEDS_RECONCILIATION';task['reason']='Some native tool calls lack a matching completion observation; do not replay them.'
            self._unknown(task)
            return self._receipt(task)
        if time.time()-task['created']>task['max_seconds']:
            task['status']='BUDGET_EXHAUSTED';task['reason']='native task execution time budget exhausted'
            return self._receipt(task)
        try:
            for row in binding['inputs']:
                if fingerprint(workspace,row['path'])!=row:raise ValueError('read-only input changed: '+row['path'])
            review=root/'trusted-review.py'
            if hashlib.sha256(review.read_bytes()).hexdigest()!=binding['review_sha256']:raise ValueError('frozen reviewer changed')
            # Use the existing challenge harness: frozen candidate, real unittest,
            # UTF-8 result channel, process/time bounds and binding checks.
            import sys
            scripts=Path(__file__).resolve().parents[1]/'scripts'
            if str(scripts) not in sys.path:sys.path.insert(1,str(scripts))
            import challenge
            packet=challenge.candidate_packet(workspace,[r['path'] for r in binding['inputs']],binding['outputs'])
            candidate=digest(packet)
            prior=next((r for r in task['reviews'] if r['candidate']==candidate),None)
            if prior and prior['status']!='CHALLENGE_PASS':
                summary=prior
            else:
                remaining=max(1,min(30,task['max_seconds']-(time.time()-task['created'])))
                summary=challenge.verify(workspace,[r['path'] for r in binding['inputs']],binding['outputs'],review,root/('review-'+uuid.uuid4().hex+'.json'),timeout=remaining)
                summary={**summary,'candidate':candidate};task['reviews'].append(summary)
            if summary['status'] in {'UNKNOWN','STALE'}:
                task['status']='NEEDS_RECONCILIATION';task['reason']='Independent verification outcome '+summary['status']+'; do not rerun unknown effects.'
                return self._receipt(task)
            if summary['status']!='CHALLENGE_PASS':
                actual=json.loads(Path(summary['receipt']).read_text(encoding='utf-8'))
                diagnostics=actual.get('stderr',{}).get('text','')[-4000:]
                raise ValueError('independent acceptance failed: '+json.dumps(summary,ensure_ascii=False)[-2000:]+'\n'+diagnostics)
            if not challenge.current(packet) or challenge.status(summary['receipt'])['status']!='CHALLENGE_PASS':raise ValueError('review or candidate became stale')
            state=rt.get(task['task_id'])
            for check in state['acceptance']:
                name=check['assertion']['path'];artifact=next(p for p in packet['outputs'] if p['path']==name)
                reference={'id':'native-readback-'+uuid.uuid4().hex,'kind':'native-independent-readback','uri':name,
                    'sha256':artifact['sha256'],'claim':check['expected'],'verdict':'PASS','check_id':check['id'],
                    'environment':'local',**{k:state[k] for k in ('task_id','revision','contract_hash','candidate')}}
                rt.apply(task['task_id'],task['lease_id'],'evidence',reference)
            mechanical=make_verifier(state['contract'],workspace,reviewer_id='native-independent-reviewer')
            def fresh(reference):
                return (challenge.current(packet) and challenge.status(summary['receipt'])['status']=='CHALLENGE_PASS'
                    and hashlib.sha256(review.read_bytes()).hexdigest()==binding['review_sha256']
                    and all(fingerprint(workspace,row['path'])==row for row in binding['inputs']) and mechanical(reference))
            _,response=rt.apply(task['task_id'],task['lease_id'],'finish',{'executor_stopped':True},
                evidence_root=workspace,reviewer_id='native-independent-reviewer',verify_evidence=fresh)
            if response.get('status')!='COMPLETED':raise ValueError('control plane refused completion')
            task['status']='COMPLETED';task['reason']='original-goal candidate passed independent acceptance'
            task['completed_packet']=packet
            rt.release(task['task_id'],task['lease_id'])
        except (ValueError,OSError,KeyError) as exc:
            task['reason']=str(exc)
            if task['repairs']>=task['max_repairs']:
                task['status']='UNVERIFIED'
            else:task['repairs']+=1
        return self._receipt(task)

    def hook(self,payload):
        event=payload.get('hook_event_name');session_id=payload.get('session_id')
        if not isinstance(session_id,str) or not session_id:return {}
        if event=='UserPromptSubmit':
            prompt=payload.get('prompt','')
            continuation=prompt.startswith(('AGI-like acceptance rejected completion.','AGI-like stopped:','AGI-like was invoked and task edits were observed,'))
            hint=hint_path(session_id);hint.parent.mkdir(parents=True,exist_ok=True)
            save(hint,{'session_id':session_id,'home':str(self.home),'created':time.time()})
            with self.db() as db:
                task=self.load(db,session_id)
                if task and task['status'] in {'COMPLETED','CANCELLED'}:
                    db.execute('INSERT OR REPLACE INTO history VALUES(?,?,?)',(task['task_id'],session_id,json.dumps(task,ensure_ascii=False)))
                    db.execute('DELETE FROM sessions WHERE id=?',(session_id,));task=None
                if task and task['status']=='RUNNING' and not continuation:
                    task['user_messages']+=1;self.store(db,session_id,task)
                if not task or task['status'] in {'COMPLETED','CANCELLED'}:
                    if isinstance(prompt,str) and 0<len(prompt)<=65536 and not continuation:
                        db.execute('INSERT OR REPLACE INTO prompts VALUES(?,?)',(session_id,json.dumps({'goal':prompt,'cwd':str(Path(payload['cwd']).resolve()),'created':time.time(),'activation_requested':'$agi-like' in prompt})))
            return {'hookSpecificOutput':{'hookEventName':event,'additionalContext':
                'AGI-like native Runtime available. Prefer MCP tools native_start/native_status/native_verify from agi_like_runtime when available; they work without shell access to Skill scripts. Pass the actual session id below and keep native business tools. CLI fallback entry: '+str(Path(__file__).resolve().parents[1]/'scripts/native_task.py')+
                '. For a multi-step deliverable handled by agi-like, call that actual script with --home "'+str(self.home)+'" begin before editing; reuse its task, verify and repair before finishing. Do not search for native_task.py in the business workspace, or start a second model loop/task for the same goal. Simple questions need no registration. Session id: '+session_id}}
        if event=='Stop':
            value=self.verify(session_id,at_stop=True)
            if value['status']=='NOT_REGISTERED':
                with self.db() as db:
                    row=db.execute('SELECT value FROM prompts WHERE id=?',(session_id,)).fetchone()
                    pending=json.loads(row[0]) if row else {}
                    if pending.get('activation_requested') and pending.get('edit_observed') and not pending.get('registration_reminded'):
                        pending['registration_reminded']=True
                        db.execute('INSERT OR REPLACE INTO prompts VALUES(?,?)',(session_id,json.dumps(pending)))
                        return {'decision':'block','reason':'AGI-like was invoked and task edits were observed, but no native Runtime task exists. Read the native Skill entry, prepare/reuse independent acceptance, and call native_task.py begin for this same original goal. Use --home '+str(self.home)+' if default state resolution is unavailable. Do not ask the user to fill technical configuration or claim the Runtime was connected.'}
                return {}
            if value['status']=='COMPLETED':return {'systemMessage':'AGI-like independent acceptance PASS. Receipt: '+str(Path(value['root'])/'receipt.json')}
            reason=value.get('reason','Task is not verified')
            if value['status']=='RUNNING':return {'decision':'block','reason':'AGI-like acceptance rejected completion. Repair the reported defect in this same task, then finish again. No user confirmation is needed for authorized repairs. '+reason}
            # One continuation makes the truthful final status model-visible;
            # subsequent Stop calls do not create an infinite repair loop.
            with self.db() as db:
                task=self.load(db,session_id)
                if not task.get('handoff_sent'):
                    task['handoff_sent']=True;self.store(db,session_id,task)
                    return {'decision':'block','reason':'AGI-like stopped: '+value['status']+'. Report the unmet goal and exact evidence; do not claim completion, reset the budget, or replay unknown effects. '+reason}
            return {'systemMessage':'AGI-like task remains '+value['status']+': '+reason}
        if event not in {'PreToolUse','PostToolUse','Interrupt'}:return {}
        notice={}
        with self.db() as db:
            task=self.load(db,session_id)
            if not task:
                if event=='PostToolUse':
                    row=db.execute('SELECT value FROM prompts WHERE id=?',(session_id,)).fetchone()
                    if row:
                        pending=json.loads(row[0]);command=re.sub('/+','/',str(payload.get('tool_input','')).replace('\\','/')).casefold()
                        if 'agi-like/skill.md' in command:pending['activation_requested']=True
                        if (payload.get('tool_name') in {'apply_patch','Edit','Write'} or re.search(r'set-content|write_(?:text|bytes)\s*\(|write_json',command)):
                            pending['edit_observed']=True
                        db.execute('INSERT OR REPLACE INTO prompts VALUES(?,?)',(session_id,json.dumps(pending)))
                return {}
            if task['status'] in {'COMPLETED','CANCELLED'}:return {}
            if event=='PreToolUse' and task['status']!='RUNNING':
                return {'hookSpecificOutput':{'hookEventName':event,'permissionDecision':'deny',
                    'permissionDecisionReason':'AGI-like task is '+task['status']+'; do not continue effects or reset its budget.'}}
            key=payload.get('tool_use_id')
            if event=='PreToolUse':
                if task['tool_count']>=task['max_tools'] or time.time()-task['created']>task['max_seconds']:
                    task['status']='BUDGET_EXHAUSTED';self.store(db,session_id,task)
                    return {'hookSpecificOutput':{'hookEventName':event,'permissionDecision':'deny','permissionDecisionReason':'AGI-like native task budget exhausted'}}
                if key and key not in task['intents']:
                    task['intents'][key]={'tool':payload.get('tool_name'),'input_sha256':digest(payload.get('tool_input'))}
                    task['tool_count']+=1
            elif event=='PostToolUse':
                if not task['hook_observed']:
                    notice={'hookSpecificOutput':{'hookEventName':event,'additionalContext':
                        'AGI-like native hooks connected to task '+task['task_id']+'. Keep using this conversation and its tools; Stop will independently verify and return repair feedback. No second model loop is needed.'}}
                task['hook_observed']=True
                if key and key not in task['hook_events']:
                    task['intents'].pop(key,None)
                    observation={'tool':payload.get('tool_name'),'tool_use_id':key,'response_sha256':digest(payload.get('tool_response'))}
                    response=payload.get('tool_response')
                    if ((isinstance(response,dict) and response.get('session_id') is not None and response.get('exit_code') is None)
                            or re.search(r'(?:Process|Script) running with (?:cell|session) ID',str(response))):
                        task['pending'][key]={'tool':payload.get('tool_name'),'reason':'native tool returned a still-running process; completion must be reconciled'}
                    task['hook_events'][key]=observation
                    rt=self.control(task);rt.heartbeat(task['task_id'],task['lease_id'],ttl_seconds=task['max_seconds']+60)
                    rt.apply(task['task_id'],task['lease_id'],'observe',observation,request_id='native-'+key)
            elif event=='Interrupt' and (task['pending'] or task.get('intents')):
                task['pending'].update(task['intents']);task['intents']={}
                task['status']='NEEDS_RECONCILIATION';task['reason']='Interrupted with unresolved native tool calls; verify actual effects before any retry.'
                self._unknown(task)
            self.store(db,session_id,task)
            if event=='PostToolUse' and task.get('verify_requested') and not task['pending'] and not task.get('intents') and task['status']=='RUNNING':
                notice={'hookSpecificOutput':{'hookEventName':event,'additionalContext':'Native tool observations are complete. The requested verification will run at Stop; finish normally rather than polling verify again. Failed acceptance will continue this same conversation for repair.'}}
        return notice

    def _unknown(self,task):
        rt=self.control(task)
        for key in task['pending']:
            rt.apply(task['task_id'],task['lease_id'],'tool_result',{'tool_call_id':key,'status':'UNKNOWN'},request_id='native-unknown-'+key)
