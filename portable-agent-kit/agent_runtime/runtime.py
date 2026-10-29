"""Model/tool/memory loop with optional durable checkpoints and host gates."""
from copy import deepcopy
from dataclasses import dataclass
import json
import uuid
import time
from .tools import ToolError
from .journal import JournalError
from .recipes import SkillPreflightError,recipe_procedure


@dataclass
class RuntimeResult:
    status: str
    task_id: str
    steps: int
    answer: str
    events: list
    invocation_id: str = ''
    checkpoint_generation: int = None


class AgentRuntime:
    def __init__(self,model,tools,memory,skills=None,*,max_steps=12,skill_library=None):
        if isinstance(max_steps,bool) or not isinstance(max_steps,int) or max_steps<1:
            raise ValueError('max_steps must be a positive integer')
        self.model=model;self.tools=tools;self.memory=memory
        self.skills=skills or {};self.max_steps=max_steps;self.skill_library=skill_library

    def run(self,goal,*,task_id=None,control=None,lease_id=None,evidence_root=None,
            reviewer_id=None,verifier=None,journal=None,resume=False,steps_per_run=None,run_context=None,
            auto_verify=False,invocation_id=None):
        if (control is None)!=(lease_id is None):raise ValueError('control and lease_id must be supplied together')
        if resume and (journal is None or not task_id):raise ValueError('resume requires journal and task_id')
        if steps_per_run is not None and (type(steps_per_run)!=int or steps_per_run<1):
            raise ValueError('steps_per_run must be a positive integer')
        if type(auto_verify) is not bool or (auto_verify and not callable(getattr(verifier,'ready',None))):
            raise ValueError('auto_verify requires a trusted verifier.ready callback')
        task_id=task_id or 'runtime-'+uuid.uuid4().hex[:12]
        invocation_id=invocation_id or uuid.uuid4().hex
        skill_manifest=list(self.skills)
        if self.skill_library:
            skill_manifest={row['name']:{'description':row['description'],
                'parameters':row['spec'].get('parameters',[]),'version':row['version'],
                'spec_sha256':row['spec_sha256'],'procedure':recipe_procedure(row['spec'])}
                for row in self.skill_library.list_active() if row['name'] in self.skills}
        current=control.get(task_id) if control else None
        binding={'goal':goal,'max_steps':self.max_steps,'tools':self.tools.manifest(),
                 'skills':skill_manifest,'context':run_context or {},'memory_scope':self.memory.scope,'auto_verify':auto_verify,
                 'control':{k:current[k] for k in ('contract_hash','candidate','revision')} if current else None}
        saved=None
        if journal:
            if resume:
                saved=journal.check_binding(task_id,binding)
            else:
                initial={'phase':'READY','next_step':1,'events':[],'pending':None}
                if control:
                    initial['budget_clock']={'started_at':time.time(),'max_seconds':current['contract']['budget']['max_seconds']}
                saved=journal.create(task_id,binding,initial)
        snapshot=deepcopy(saved['snapshot']) if saved else {'phase':'READY','next_step':1,'events':[],'pending':None}
        if control and not journal:
            snapshot['budget_clock']={'started_at':time.time(),'max_seconds':current['contract']['budget']['max_seconds']}
        def remaining_seconds():
            clock=snapshot.get('budget_clock')
            if not clock:return None
            now=time.time()
            if now<clock['started_at']:return 0.0
            return max(0.0,clock['started_at']+clock['max_seconds']-now)
        events=snapshot['events']
        def _result(status,step,answer):
            owned=snapshot.get('invocation_id')==invocation_id
            return RuntimeResult(status,task_id,step,answer,deepcopy(events),invocation_id,
                                 saved['generation'] if saved and owned else None)
        def persist():
            nonlocal saved
            if journal:saved=journal.save(task_id,saved['generation'],snapshot)
        def end(status,step,answer):
            snapshot['last_status']=status;snapshot['answer']=answer
            if status=='COMPLETED':snapshot['phase']='FINISHED'
            if status=='CANCELLED':snapshot['phase']='CANCELLED'
            if (control and status in {'MODEL_ERROR','BUDGET_EXHAUSTED','CONTROL_BLOCKED','FAILED','UNVERIFIED'}
                    and snapshot['phase'] in {'READY','PAUSED'} and snapshot.get('pending') is None):
                # The live loop has stopped, so known failures can release their
                # executor lease without falsely claiming an unknown action is
                # safe. Preserve the error as last_status for the operator.
                try:
                    latest=control.get(task_id)
                    if latest['status'] in {'RUNNING','BLOCKED'}:
                        token=uuid.uuid4().hex
                        control.apply(task_id,lease_id,'checkpoint',
                            {'id':'safe-stop-'+token,'resume_token':token,
                             'cursor':{'next_step':snapshot['next_step'],'last_status':status}})
                        control.apply(task_id,lease_id,'pause',{'executor_stopped':True})
                        snapshot['phase']='PAUSED'
                    elif latest['status']=='PAUSED':snapshot['phase']='PAUSED'
                except Exception as exc:
                    # An expired/lost lease cannot be used to change ownership.
                    # The host must inspect/reclaim it; do not report PAUSED.
                    snapshot['safe_stop_error']=type(exc).__name__+': '+str(exc)
            persist()
            return _result(status,step,answer)
        def stop_requested(next_step,completed_steps,*,discarded=None):
            nonlocal saved
            if journal is None:return None
            request=journal.pending_control(task_id)
            if request is None:return None
            latest=control.get(task_id) if control else None
            if latest and latest['status']=='UNKNOWN':
                return end('NEEDS_RECONCILIATION',completed_steps,'unknown control-plane outcome must be reconciled before stopping')
            if latest and latest['status'] in {'FAILED','COMPLETED'}:
                return end('CONTROL_BLOCKED',completed_steps,'control state '+latest['status'])
            if discarded is not None:
                events.append({'step':completed_steps,'action':'model_discarded',
                               'reason':'cooperative '+request['operation']+' request',
                               'decision':deepcopy(discarded) if isinstance(discarded,dict) else {'type':type(discarded).__name__}})
            # This helper is invoked only by the live executor after its model,
            # tool or verifier returned. New processes never call it for an
            # unfinished MODEL/ACTION/FINISH_PENDING checkpoint.
            snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=next_step
            snapshot['controls_closed']=False;persist()
            operation=request['operation'];request_key='control-'+request['request_id']
            if control:
                latest=control.get(task_id)
                if latest['status']=='CANCELLED':operation='cancel'
                if operation=='pause':
                    if not latest['started']:
                        control.apply(task_id,lease_id,'start',{'executor':'agent-runtime'},request_id=task_id+'-start')
                    if latest['status']!='PAUSED':
                        token=uuid.uuid5(uuid.NAMESPACE_URL,task_id+'/'+request['request_id']).hex
                        control.apply(task_id,lease_id,'checkpoint',
                            {'id':request_key,'resume_token':token,'cursor':{'next_step':next_step}},
                            request_id=request_key+'-checkpoint')
                        control.apply(task_id,lease_id,'pause',{'executor_stopped':True},request_id=request_key+'-pause')
                elif latest['status']!='CANCELLED':
                    control.apply(task_id,lease_id,'cancel',
                        {'explicit':True,'executor_stopped':True,'reason':request['reason']},request_id=request_key+'-cancel')
            status='PAUSED' if operation=='pause' else 'CANCELLED'
            answer='cooperative '+operation+' acknowledged at a safe boundary: '+request['reason']
            snapshot.update(phase=status,last_status=status,answer=answer)
            snapshot.setdefault('control_acknowledgements',[]).append({
                'request_id':request['request_id'],'operation':request['operation'],'reason':request['reason'],
                'status':status,'next_step':next_step})
            saved=journal.acknowledge_control(task_id,request['request_id'],saved['generation'],snapshot)
            return _result(status,completed_steps,answer)
        def prepare_finish():
            nonlocal saved
            if journal is None:return None
            try:
                saved=journal.prepare_finish(task_id,saved['generation'],snapshot)
            except JournalError as exc:
                if str(exc)!='CONTROL_REQUEST_PENDING':raise
                outcome=stop_requested(snapshot['next_step']+1,snapshot['next_step'])
                if outcome:return outcome
                raise
            snapshot['controls_closed']=True
            return None
        if snapshot['phase'] in {'ACTION_PENDING','MODEL_PENDING','FINISH_PENDING'}:
            return _result('NEEDS_RECONCILIATION',snapshot['next_step'],
                           'unfinished action in checkpoint; verify readback before resuming')
        if snapshot['phase']=='FINISHED':raise ValueError('run is terminal; open a new task for new work')
        if snapshot['phase']=='CANCELLED':
            return _result('CANCELLED',max(0,snapshot['next_step']-1),snapshot.get('answer','run already cancelled'))
        snapshot['invocation_id']=invocation_id
        stopped=stop_requested(snapshot['next_step'],max(0,snapshot['next_step']-1))
        if stopped:return stopped
        if control and not snapshot.get('budget_clock'):
            return end('CONTROL_BLOCKED',max(0,snapshot['next_step']-1),'legacy checkpoint lacks a persistent wall budget; use its original runtime or an explicit migration')
        review_limit=current['contract']['budget']['max_retries'] if control else 2
        if snapshot.get('rejections',0)>review_limit:
            return end('CONTROL_BLOCKED',max(0,snapshot['next_step']-1),'independent review retry budget exhausted; a resumed process cannot reset it')
        if snapshot['next_step']>self.max_steps:
            return end('BUDGET_EXHAUSTED',self.max_steps,'model step budget exhausted; a resumed process cannot reset it')
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
            stopped=stop_requested(step,step-1)
            if stopped:return stopped
            remaining=remaining_seconds()
            if remaining is not None and remaining<=0:
                return end('BUDGET_EXHAUSTED',step-1,'task wall-time budget exhausted; resume cannot reset it')
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
                     'tools':self.tools.manifest(),'skills':skill_manifest,'observations':deepcopy(events),
                     'budget':{'max_steps':self.max_steps,'steps_remaining':self.max_steps-step+1,
                               'remaining_seconds':remaining,
                               'review_rejections':snapshot.get('rejections',0)},
                     'completion_mode':'host-verifies-changed-artifacts' if auto_verify else 'model-proposes-finish'}
            if control:
                request['acceptance']=deepcopy(current['acceptance'])
                reads=[e for e in events if e.get('action')=='tool' and e.get('decision',{}).get('name') in {'read_file','read_json'}
                       and 'result' in e and not (isinstance(e['result'],dict) and e['result'].get('status') in {'FAILED','UNKNOWN'})]
                if len(reads)>=2 and reads[-1]['decision'].get('arguments')==reads[-2]['decision'].get('arguments') and reads[-1]['result']==reads[-2]['result']:
                    seen={e['decision'].get('arguments',{}).get('path') for e in reads}
                    missing=[name for name in current['contract'].get('inputs',[]) if name not in seen]
                    request['runtime_feedback']={'decision':'REPLAN_REQUIRED','reason':'Repeated read returned identical evidence. The file has already been read; repeating it is not progress.',
                        'already_read':sorted(name for name in seen if isinstance(name,str)),'remaining_required_inputs':missing,
                        'next_action':'Read the first remaining required input, then execute the requested workflow. If none remain, use the observed data and available skill parameters; do not restart TASK.md instructions.'}
                request['bindings']={k:current[k] for k in ('task_id','revision','contract_hash','candidate')}
                if current['contract'].get('intake',{}).get('plan'):
                    request['proposed_plan']={'authority':'untrusted planning advice; original goal and host contract prevail',
                                              **deepcopy(current['contract']['intake']['plan'])}
            snapshot['phase']='MODEL_PENDING';snapshot['next_step']=step;persist()
            try:decision=self.model(request)
            except Exception as exc:
                if getattr(exc,'outcome_unknown',False) is True:
                    # A decision host may expose native tools outside this registry.
                    # Never turn an observed or possibly incomplete host action into
                    # a retryable model error: preserve a bound readback obligation.
                    action_id=task_id+'-step-'+str(step)+'-model-host'
                    decision={'action':'unmanaged_host','name':'model-host','arguments':{}}
                    event={'step':step,'action':'unmanaged_host','decision':decision,
                           'result':{'status':'UNKNOWN','error':str(exc),
                                     'details':deepcopy(getattr(exc,'details',{}))}}
                    events.append(event)
                    snapshot['phase']='ACTION_PENDING'
                    snapshot['pending']={'id':action_id,'decision':deepcopy(decision),
                                         'host_evidence':deepcopy(getattr(exc,'details',{}))}
                    persist()
                    if control:
                        control.apply(task_id,lease_id,'tool_result',
                            {'tool_call_id':action_id,'status':'UNKNOWN'},request_id=action_id)
                    return end('NEEDS_RECONCILIATION',step,'decision host outcome unknown; verify host logs and readback before resuming')
                snapshot['phase']='READY';snapshot['next_step']=step+1
                stopped=stop_requested(step+1,step,discarded={'error':str(exc)})
                if stopped:return stopped
                return end('MODEL_ERROR',step,str(exc))
            stopped=stop_requested(step+1,step,discarded=decision)
            if stopped:return stopped
            remaining=remaining_seconds()
            if remaining is not None and remaining<=0:
                events.append({'step':step,'action':'model_discarded','reason':'task wall-time budget exhausted'})
                snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1
                return end('BUDGET_EXHAUSTED',step,'task wall-time budget exhausted before action dispatch')
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
            if action=='skill' and isinstance(skill_manifest,dict) and decision.get('name') in skill_manifest:
                selected=skill_manifest[decision['name']]
                event['skill_binding']={'name':decision['name'],'version':selected['version'],
                                        'spec_sha256':selected['spec_sha256']}
            events.append(event)
            action_id=task_id+'-step-'+str(step)
            if control and action in {'tool','skill'}:
                budget=latest.get('control_plane',{})
                used=budget.get('steps_used',0)
                limit=budget.get('max_steps',latest['contract']['budget']['max_steps'])
                if used>=limit:
                    # No action is pending or has run. Checking only when
                    # recording its result would discover exhaustion too late,
                    # after a real side effect might already have happened.
                    event['result']={'status':'REJECTED','reason':'control-plane work budget exhausted',
                                     'pre_effect_rejection':True}
                    snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1
                    return end('BUDGET_EXHAUSTED',step,'control-plane work budget exhausted before tool execution')
            snapshot['phase']='ACTION_PENDING';snapshot['pending']={'id':action_id,'decision':deepcopy(decision)}
            if journal:
                try:saved=journal.prepare_action(task_id,saved['generation'],snapshot)
                except JournalError as exc:
                    if str(exc)!='CONTROL_REQUEST_PENDING':raise
                    events.pop()  # The decision never acquired dispatch ownership.
                    stopped=stop_requested(step+1,step,discarded=decision)
                    if stopped:return stopped
                    raise
            else:persist()
            remaining=remaining_seconds()
            if remaining is not None and remaining<=0:
                event['result']={'status':'REJECTED','reason':'wall-time budget exhausted before dispatch','pre_effect_rejection':True}
                snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1
                return end('BUDGET_EXHAUSTED',step,'task wall-time budget exhausted before action dispatch')
            if action in {'tool','skill'}:
                self.tools.execution_context={'task_id':task_id,'action_id':action_id}
                if action=='skill':self.tools.begin_capture()
                pre_effect_rejection=False
                try:
                    if action=='tool':result=self.tools.call(decision['name'],decision.get('arguments',{}))
                    else:
                        if decision.get('name') not in self.skills:raise ValueError('unknown skill')
                        result=self.skills[decision['name']](decision.get('arguments',{}))
                except SkillPreflightError as exc:
                    result={'status':'FAILED','error':str(exc),'pre_effect_rejection':True}
                    pre_effect_rejection=True
                except ToolError as exc:
                    # A multi-step skill may already have completed earlier
                    # tools when a later tool rejects. Keep its effects unknown.
                    result={'status':'UNKNOWN' if action=='skill' else 'FAILED','error':str(exc)}
                    pre_effect_rejection=action=='tool'
                except Exception as exc:result={'status':'UNKNOWN','error':type(exc).__name__+': '+str(exc)}
                finally:
                    if action=='skill':event['tool_observations']=self.tools.end_capture()
                event['result']=result
                if control:
                    status=result.get('status','SUCCEEDED') if isinstance(result,dict) else 'SUCCEEDED'
                    if status not in {'SUCCEEDED','FAILED','UNKNOWN'}:status='UNKNOWN'
                    payload={'tool_call_id':action_id,'status':status}
                    if pre_effect_rejection:payload['pre_effect_rejection']=True
                    control.apply(task_id,lease_id,'tool_result',payload,request_id=action_id)
                if isinstance(result,dict) and result.get('status')=='UNKNOWN':
                    return end('NEEDS_RECONCILIATION',step,'tool outcome unknown; verify readback before retry')
                self.memory.add('%s %s: %s'%(action,decision.get('name'),json.dumps(result,ensure_ascii=False)),
                    kind='episode' if action=='tool' else 'skill-use',task_id=task_id,source_refs=['task:'+task_id+'/step/'+str(step)])
                snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1;persist()
                stopped=stop_requested(step+1,step)
                if stopped:return stopped
                if auto_verify and not pre_effect_rejection and (not isinstance(result,dict) or result.get('status','SUCCEEDED')=='SUCCEEDED'):
                    try:ready=verifier.ready(deepcopy(events)) is True
                    except Exception:ready=False
                    if ready:
                        # The host, not the model, may recognize that a changed
                        # candidate is complete. Reuse the exact finish gate.
                        action='finish'
                        decision={'action':'finish','answer':'Host independently verified the declared goal artifacts.'}
                        event={'step':step,'action':'finish','source':'host-artifact-review','decision':decision}
                        events.append(event)
            if action=='evidence':
                if not(control and evidence_root and reviewer_id):
                    snapshot['phase']='READY';snapshot['pending']=None
                    return end('FAILED',step,'evidence requires control and independent verifier')
                _,event['result']=control.apply(task_id,lease_id,'evidence',decision.get('evidence',{}),
                    evidence_root=evidence_root,reviewer_id=reviewer_id,request_id=action_id)
            elif action=='finish':
                snapshot['next_step']=step
                snapshot['phase']='FINISH_PENDING';persist();answer=str(decision.get('answer',''))
                stopped=stop_requested(step+1,step)
                if stopped:return stopped
                def reject(reason):
                    snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1
                    snapshot['controls_closed']=False
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
                            if getattr(exc,'outcome_unknown',False) is True:
                                return end('NEEDS_RECONCILIATION',step,'verifier outcome unknown; '+str(exc))
                            stopped=stop_requested(step+1,step)
                            if stopped:return stopped
                            outcome=reject(str(exc))
                            if outcome:return outcome
                            executed+=1;continue
                        stopped=stop_requested(step+1,step)
                        if stopped:return stopped
                        if not accepted:
                            outcome=reject('host readback or independent tests rejected completion')
                            if outcome:return outcome
                            executed+=1;continue
                    stopped=prepare_finish()
                    if stopped:return stopped
                    _,response=control.apply(task_id,lease_id,'finish',{'executor_stopped':True},
                        evidence_root=evidence_root,reviewer_id=reviewer_id,
                        verify_evidence=getattr(verifier,'finish_verify',None))
                    if response.get('status')!='COMPLETED':
                        outcome=reject(str(response.get('status')))
                        if outcome:return outcome
                        executed+=1;continue
                else:
                    try:accepted=callable(verifier) and verifier(goal,answer,deepcopy(events)) is True
                    except Exception as exc:
                        if getattr(exc,'outcome_unknown',False) is True:
                            return end('NEEDS_RECONCILIATION',step,'verifier outcome unknown; '+str(exc))
                        accepted=False
                    stopped=stop_requested(step+1,step)
                    if stopped:return stopped
                    if not accepted:
                        snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1
                        return end('UNVERIFIED',step,answer)
                    stopped=prepare_finish()
                    if stopped:return stopped
                self.memory.add(goal,kind='success',task_id=task_id,data={'answer':answer,'events':events},source_refs=['task:'+task_id+'/accepted'])
                if self.skill_library:
                    self.skill_library.record_experience(task_id,goal,events,success=True)
                    self.skill_library.propose_from_repeated_success(goal)
                snapshot['pending']=None;snapshot['next_step']=step+1
                return end('COMPLETED',step,answer)
            elif action not in {'tool','skill'}:
                snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1
                return end('FAILED',step,'unsupported action: '+str(action))
            executed+=1;snapshot['phase']='READY';snapshot['pending']=None;snapshot['next_step']=step+1;persist()
        stopped=stop_requested(snapshot['next_step'],min(self.max_steps,snapshot['next_step']-1))
        if stopped:return stopped
        return end('BUDGET_EXHAUSTED',self.max_steps,'model step budget exhausted')
