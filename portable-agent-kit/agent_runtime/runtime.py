"""Model/tool/memory loop with optional durable checkpoints and host gates."""
from copy import deepcopy
from dataclasses import dataclass
import json
import uuid
from .tools import ToolError


@dataclass
class RuntimeResult:
    status: str
    task_id: str
    steps: int
    answer: str
    events: list


class AgentRuntime:
    def __init__(self,model,tools,memory,skills=None,*,max_steps=12,skill_library=None):
        if isinstance(max_steps,bool) or not isinstance(max_steps,int) or max_steps<1:
            raise ValueError('max_steps must be a positive integer')
        self.model=model;self.tools=tools;self.memory=memory
        self.skills=skills or {};self.max_steps=max_steps;self.skill_library=skill_library

    def run(self,goal,*,task_id=None,control=None,lease_id=None,evidence_root=None,
            reviewer_id=None,verifier=None,journal=None,resume=False,steps_per_run=None,run_context=None):
        if (control is None)!=(lease_id is None):raise ValueError('control and lease_id must be supplied together')
        if resume and (journal is None or not task_id):raise ValueError('resume requires journal and task_id')
        if steps_per_run is not None and (type(steps_per_run)!=int or steps_per_run<1):
            raise ValueError('steps_per_run must be a positive integer')
        task_id=task_id or 'runtime-'+uuid.uuid4().hex[:12]
        skill_manifest=list(self.skills)
        if self.skill_library:
            skill_manifest={row['name']:{'description':row['description'],
                'parameters':row['spec'].get('parameters',[]),'version':row['version']}
                for row in self.skill_library.list_active() if row['name'] in self.skills}
        current=control.get(task_id) if control else None
        binding={'goal':goal,'max_steps':self.max_steps,'tools':self.tools.manifest(),
                 'skills':skill_manifest,'context':run_context or {},'memory_scope':self.memory.scope,
                 'control':{k:current[k] for k in ('contract_hash','candidate','revision')} if current else None}
        saved=None
        if journal:
            if resume:
                saved=journal.check_binding(task_id,binding)
            else:
                saved=journal.create(task_id,binding,{'phase':'READY','next_step':1,'events':[],'pending':None})
        snapshot=deepcopy(saved['snapshot']) if saved else {'phase':'READY','next_step':1,'events':[],'pending':None}
        events=snapshot['events']
        def persist():
            nonlocal saved
            if journal:saved=journal.save(task_id,saved['generation'],snapshot)
        def end(status,step,answer):
            snapshot['last_status']=status;snapshot['answer']=answer
            if status=='COMPLETED':snapshot['phase']='FINISHED'
            persist()
            return RuntimeResult(status,task_id,step,answer,deepcopy(events))
        if snapshot['phase'] in {'ACTION_PENDING','MODEL_PENDING','FINISH_PENDING'}:
            return RuntimeResult('NEEDS_RECONCILIATION',task_id,snapshot['next_step'],
                                 'unfinished action in checkpoint; verify readback before resuming',deepcopy(events))
        if snapshot['phase']=='FINISHED':raise ValueError('run is terminal; open a new task for new work')
        if control:
            if not current['started']:
                control.apply(task_id,lease_id,'start',{'executor':'agent-runtime'},request_id=task_id+'-start')
            elif current['status']=='PAUSED' and resume:
                control.apply(task_id,lease_id,'resume',{'resume_token':current['checkpoints'][-1]['resume_token'],
                    'executor':'agent-runtime','executor_verified':True})
            elif not resume:
                raise ValueError('control task already started; explicit resume is required')
        snapshot['phase']='READY';persist()
        executed=0
        for step in range(snapshot['next_step'],self.max_steps+1):
            if steps_per_run is not None and executed>=steps_per_run:
                if control:
                    token=uuid.uuid4().hex
                    control.apply(task_id,lease_id,'checkpoint',{'id':'loop-step-'+str(step),
                        'resume_token':token,'cursor':{'next_step':step}})
                    control.apply(task_id,lease_id,'pause',{'executor_stopped':True})
                snapshot['phase']='PAUSED'
                return end('PAUSED',step-1,'safe checkpoint saved')
            if control:
                current=control.get(task_id)
                if current['status'] in {'CANCELLED','FAILED','COMPLETED','PAUSED','UNKNOWN'}:
                    return end('CONTROL_BLOCKED',step-1,'control state '+current['status'])
            request={'task_id':task_id,'goal':goal,'step':step,'memory':self.memory.search(goal),
                     'tools':self.tools.manifest(),'skills':skill_manifest,'observations':deepcopy(events)}
            if control:
                request['acceptance']=deepcopy(current['acceptance'])
                request['bindings']={k:current[k] for k in ('task_id','revision','contract_hash','candidate')}
            snapshot['phase']='MODEL_PENDING';snapshot['next_step']=step;persist()
            try:decision=self.model(request)
            except Exception as exc:
                snapshot['phase']='READY';snapshot['next_step']=step+1
                return end('MODEL_ERROR',step,str(exc))
            if not isinstance(decision,dict):
                snapshot['phase']='READY';snapshot['next_step']=step+1
                return end('FAILED',step,'model decision must be an object')
            if control:
                try:
                    control.heartbeat(task_id,lease_id)
                except Exception as exc:
                    snapshot['phase']='READY';snapshot['next_step']=step+1
                    return end('LEASE_LOST',step,str(exc))
                latest=control.get(task_id)
                if latest['status'] in {'CANCELLED','FAILED','COMPLETED','PAUSED','UNKNOWN'}:
                    snapshot['phase']='READY';snapshot['next_step']=step+1
                    return end('CONTROL_BLOCKED',step,'control state '+latest['status'])
            action=decision.get('action');event={'step':step,'action':action,'decision':decision}
            events.append(event)
            action_id=task_id+'-step-'+str(step)
            snapshot['phase']='ACTION_PENDING';snapshot['pending']={'id':action_id,'decision':deepcopy(decision)};persist()
            if action in {'tool','skill'}:
                try:
                    if action=='tool':result=self.tools.call(decision['name'],decision.get('arguments',{}))
                    else:
                        if decision.get('name') not in self.skills:raise ValueError('unknown skill')
                        result=self.skills[decision['name']](decision.get('arguments',{}))
                except ToolError as exc:
                    # A multi-step skill may already have completed earlier
                    # tools when a later tool rejects. Keep its effects unknown.
                    result={'status':'UNKNOWN' if action=='skill' else 'FAILED','error':str(exc)}
                except Exception as exc:result={'status':'UNKNOWN','error':type(exc).__name__+': '+str(exc)}
                event['result']=result
                if control:
                    status=result.get('status','SUCCEEDED') if isinstance(result,dict) else 'SUCCEEDED'
                    if status not in {'SUCCEEDED','FAILED','UNKNOWN'}:status='UNKNOWN'
                    control.apply(task_id,lease_id,'tool_result',{'tool_call_id':action_id,'status':status},request_id=action_id)
                if isinstance(result,dict) and result.get('status')=='UNKNOWN':
                    return end('NEEDS_RECONCILIATION',step,'tool outcome unknown; verify readback before retry')
                self.memory.add('%s %s: %s'%(action,decision.get('name'),json.dumps(result,ensure_ascii=False)),
                    kind='episode' if action=='tool' else 'skill-use',task_id=task_id,source_refs=['task:'+task_id+'/step/'+str(step)])
            elif action=='evidence':
                if not(control and evidence_root and reviewer_id):
                    snapshot['phase']='READY';snapshot['pending']=None
                    return end('FAILED',step,'evidence requires control and independent verifier')
                _,event['result']=control.apply(task_id,lease_id,'evidence',decision.get('evidence',{}),
                    evidence_root=evidence_root,reviewer_id=reviewer_id,request_id=action_id)
            elif action=='finish':
                snapshot['phase']='FINISH_PENDING';persist();answer=str(decision.get('answer',''))
                def reject(reason):
                    snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1
                    snapshot['rejections']=snapshot.get('rejections',0)+1
                    event['result']={'status':'REJECTED','reason':reason,
                                     'diagnostics':deepcopy(getattr(verifier,'last_error',None))}
                    persist()
                    limit=current['contract']['budget']['max_retries'] if control else 2
                    if step<self.max_steps and snapshot['rejections']<=limit:
                        return None
                    return end('CONTROL_BLOCKED',step,reason)
                if control:
                    if callable(verifier):
                        try:accepted=verifier(goal,answer,deepcopy(events)) is True
                        except Exception as exc:
                            outcome=reject(str(exc))
                            if outcome:return outcome
                            executed+=1;continue
                        if not accepted:
                            outcome=reject('host readback or independent tests rejected completion')
                            if outcome:return outcome
                            executed+=1;continue
                    _,response=control.apply(task_id,lease_id,'finish',{'executor_stopped':True},
                        evidence_root=evidence_root,reviewer_id=reviewer_id)
                    if response.get('status')!='COMPLETED':
                        outcome=reject(str(response.get('status')))
                        if outcome:return outcome
                        executed+=1;continue
                else:
                    try:accepted=callable(verifier) and verifier(goal,answer,deepcopy(events)) is True
                    except Exception:accepted=False
                    if not accepted:
                        snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1
                        return end('UNVERIFIED',step,answer)
                self.memory.add(goal,kind='success',task_id=task_id,data={'answer':answer,'events':events},source_refs=['task:'+task_id+'/accepted'])
                if self.skill_library:
                    self.skill_library.record_experience(task_id,goal,events,success=True)
                    self.skill_library.propose_from_repeated_success(goal)
                snapshot['pending']=None;snapshot['next_step']=step+1
                return end('COMPLETED',step,answer)
            else:
                snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1
                return end('FAILED',step,'unsupported action: '+str(action))
            executed+=1;snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1;persist()
        return end('BUDGET_EXHAUSTED',self.max_steps,'model step budget exhausted')
