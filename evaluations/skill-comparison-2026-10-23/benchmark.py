"""Preregistered exploratory three-arm trial. No candidate-specific repair hints."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import time
import traceback
from runtime import CODEX, MODEL, EFFORT, TIER, ROOT, setup, credential_cleanup, fingerprint, save
sys.path.insert(0, str(ROOT/'design'))
import fixture_tools as fixtures

ARMS=('baseline','solo','superpowers')
SEED=20261023
MAX_SECONDS=900
MAX_TURNS=3
FOLLOWUP=('同意你提出的推荐实现或设计方案，请按 TASK.md 和 CONTRACT.md 的原范围继续完成。'
          '题面已经给出的要求保持不变；未定义的非必要实现细节由你选择。'
          '我无法提供额外账号身份或权限，也没有授权对真实外部系统操作。')
COMMON=('在当前项目中完成 TASK.md 的任务。先读取公开任务与契约。'
        '可使用当前项目、它的正常 Git 工作树、当前安装的技能和本题提供的本机服务；'
        '不得读取其他任务、其他试验产物、宿主私有文件、真实用户配置/认证或判分代码。'
        '不要联网下载内容、访问真实账号、启动长期后台任务或修改全局配置。'
        '最终产物放在当前项目根目录；如在 Git 工作树交付，请在最终答复明确标出唯一交付目录。'
        '主执行者及子执行者均使用本会话已配置的同一个模型和推理强度；不切换模型。'
        '按实际完成情况答复，不要求特定报告格式。')

def utc():
    return datetime.now(timezone.utc).isoformat()

def read_events(path):
    result=[]
    if Path(path).exists():
        for line in Path(path).read_text(encoding='utf-8',errors='replace').splitlines():
            try: result.append(json.loads(line))
            except ValueError: pass
    return result

def stop_owned(process):
    if process.poll() is not None:
        return {'needed':False,'leader_stopped':True}
    cp=subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15)
    try: process.wait(timeout=10)
    except subprocess.TimeoutExpired: pass
    return {'needed':True,'returncode':cp.returncode,'leader_stopped':process.poll() is not None,
            'details':(cp.stdout+cp.stderr).decode(errors='replace')[-1000:]}

def check_submission(ctx, task, final_path):
    root_result=fixtures.evaluate(task,ctx['workspace'])
    if root_result['completed']:
        return {'selected':'root','result':root_result}
    cp=subprocess.run(['git','-C',str(ctx['workspace']),'worktree','list','--porcelain'],
                      capture_output=True,env=ctx['env'],text=True,encoding='utf-8',timeout=20)
    roots=[]
    for line in cp.stdout.splitlines():
        if line.startswith('worktree '):
            p=Path(line[9:]).resolve()
            if p!=ctx['workspace'].resolve() and p.is_relative_to(ctx['base'].resolve()): roots.append(p)
    answer=final_path.read_text(encoding='utf-8',errors='replace') if final_path.exists() else ''
    normalized=answer.replace('\\','/').casefold()
    declared=[]
    for p in roots:
        absolute=str(p).replace('\\','/').casefold()
        relative=p.relative_to(ctx['workspace']).as_posix().casefold() if p.is_relative_to(ctx['workspace']) else None
        if absolute in normalized or (relative and relative in normalized): declared.append(p)
    selected=declared[0] if len(declared)==1 else roots[0] if len(roots)==1 else None
    if selected:
        result=fixtures.evaluate(task,selected,control_workspace=ctx['workspace'])
        return {'selected':'declared-owned-worktree' if len(declared)==1 else 'unique-owned-worktree','submitted_workspace':str(selected),
                'root_result':root_result,'result':result,'worktree_list':cp.stdout}
    return {'selected':'root','result':root_result,'other_worktree_count':len(roots),
            'selection_unknown':len(roots)>1}

def run_case(task, arm, repeat):
    label=f'{task}-r{repeat}-{arm}'
    out=ROOT/'runs'/label
    out.mkdir(parents=True,exist_ok=False)
    receipt={'id':label,'task':task,'arm':arm,'repeat':repeat,'started_utc':utc(),
             'status':'starting','turns':[],'model':MODEL,'effort':EFFORT,'tier':TIER,
             'synthetic_user_replies':0,'real_user_interventions':0}
    save(out/'receipt.json',receipt)
    ctx=None; server=None; thread=None; process=None
    try:
        ctx=setup(label,arm,builder=lambda w:fixtures.build(task,w))
        if task==fixtures.TASK_IDS[2]: server,thread=fixtures.start_service(ctx['workspace'])
        subprocess.run(['git','-C',str(ctx['workspace']),'add','.'],env=ctx['env'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(ctx['workspace']),'commit','--quiet','-m','Frozen public task'],env=ctx['env'],check=True,capture_output=True)
        receipt.update(workspace=str(ctx['workspace']),home=str(ctx['home']),initial_files=fingerprint(ctx['workspace']))
        receipt['config_sha256']=hashlib.sha256((ctx['home']/'config.toml').read_bytes()).hexdigest()
        save(out/'config.json',{'text':(ctx['home']/'config.toml').read_text(encoding='utf-8')})
        base_prompt=COMMON
        if arm=='solo': base_prompt='使用 $solo-dev-delivery。\n'+base_prompt
        elif arm=='superpowers': base_prompt='使用 $using-superpowers。\n'+base_prompt
        started=time.monotonic()
        model_seconds=0.0
        receipt['execution_started_utc']=utc()
        session_id=None
        for turn in range(1,MAX_TURNS+1):
            remaining=MAX_SECONDS-model_seconds
            if remaining<=0:
                receipt['status']='budget_censored'; break
            prompt=base_prompt if turn==1 else FOLLOWUP
            prefix=out/f'turn-{turn}'
            prefix.with_suffix('.prompt.txt').write_text(prompt,encoding='utf-8')
            events_path=prefix.with_suffix('.events.jsonl')
            final_path=prefix.with_suffix('.final.txt')
            argv=[CODEX,'exec']
            if session_id: argv+=['resume','--json','--ignore-rules','-o',str(final_path),session_id,'-']
            else: argv+=['--json','--ignore-rules','-C',str(ctx['workspace']),'-o',str(final_path),'-']
            record={'number':turn,'started_utc':utc(),'synthetic_followup':turn>1,'argv':argv}
            tick=time.monotonic()
            with events_path.open('wb') as stdout,prefix.with_suffix('.stderr.log').open('wb') as stderr:
                process=subprocess.Popen(argv,cwd=ctx['workspace'],env=ctx['env'],stdin=subprocess.PIPE,
                                         stdout=stdout,stderr=stderr)
                try:
                    process.communicate(prompt.encode('utf-8'),timeout=remaining)
                except subprocess.TimeoutExpired:
                    record['cleanup']=stop_owned(process)
                    record['budget_censored']=True
            record.update(seconds=time.monotonic()-tick,returncode=process.poll(),ended_utc=utc())
            model_seconds+=record['seconds']
            events=read_events(events_path)
            for e in events:
                if e.get('type')=='thread.started': session_id=e.get('thread_id')
            record['thread_id']=session_id
            record['usage_events']=[e.get('usage') for e in events if e.get('type')=='turn.completed']
            record['errors']=[e for e in events if e.get('type') in ('error','turn.failed')]
            record['turn_completed']=any(e.get('type')=='turn.completed' for e in events)
            check_tick=time.monotonic()
            submission=check_submission(ctx,task,final_path)
            record['independent_checker_seconds']=time.monotonic()-check_tick
            save(prefix.with_suffix('.evaluation.json'),submission)
            record['business_completed']=submission['result']['completed']
            record['business_checks']={'passed':submission['result']['passed'],'total':submission['result']['total']}
            receipt['turns'].append(record)
            receipt['execution_seconds']=model_seconds
            receipt['execution_and_checker_seconds']=time.monotonic()-started
            receipt['synthetic_user_replies']=turn-1
            save(out/'receipt.json',receipt)
            if record.get('budget_censored'):
                receipt['status']='budget_censored'; break
            if process.returncode!=0 or not record['turn_completed']:
                receipt['status']='execution_error_unclassified'; break
            if record['business_completed']:
                receipt['status']='business_complete'; break
            if not session_id:
                receipt['status']='infra_error'; break
            if turn==MAX_TURNS: receipt['status']='not_complete_after_protocol'
        receipt['submission']=submission
        # Retain a submitted code diff and native task action audit, but never credentials.
        submitted=Path(submission.get('submitted_workspace',ctx['workspace']))
        cp=subprocess.run(['git','-C',str(submitted),'diff','--binary','HEAD'],
                          capture_output=True,env=ctx['env'],timeout=20)
        (out/'changes.patch').write_bytes(cp.stdout)
        receipt['final_files']=fingerprint(submitted)
        if task==fixtures.TASK_IDS[2]:
            save(out/'service-state.json',json.loads((fixtures.control_dir(ctx['workspace'])/'authority.json').read_text(encoding='utf-8')))
    except Exception as exc:
        receipt.update(status='harness_error',error=repr(exc),traceback=traceback.format_exc())
    finally:
        if process and process.poll() is None:
            receipt['cleanup']=stop_owned(process)
        if server:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        if ctx:
            credential_cleanup(ctx)
            try:
                import metrics
                receipt['metrics']=metrics.aggregate_run(sorted(out.glob('turn-*.events.jsonl')),ctx['home'])
            except Exception as exc: receipt['metrics_error']=repr(exc)
        receipt['ended_utc']=utc()
        save(out/'receipt.json',receipt)
    print(json.dumps({'id':label,'status':receipt['status'],'seconds':round(receipt.get('execution_seconds',0),1),
                      'turns':len(receipt['turns'])},ensure_ascii=True),flush=True)
    return receipt

def register():
    blocks=[{'task':t,'repeat':r} for t in fixtures.TASK_IDS for r in (1,2)]
    rng=random.Random(SEED); rng.shuffle(blocks)
    for b in blocks:
        order=list(ARMS); rng.shuffle(order); b['arms']=order
    plan={'registered_utc':utc(),'exploratory':True,'seed':SEED,'blocks':blocks,'arms':ARMS,
          'model':MODEL,'effort':EFFORT,'tier':TIER,'max_seconds_per_case':MAX_SECONDS,
          'max_turns':MAX_TURNS,'concurrent_cases':3,'agents_max_threads_config':2,'max_child_depth':1,
          'common_prompt':COMMON,'followup':FOLLOWUP,'followup_rule':'If independent business acceptance is incomplete after a clean turn, send the same non-diagnostic factual approval, at most two times. No hidden failures or repair hints are disclosed.',
          'worktree_selection':'Root if complete; otherwise accept exactly one declared owned worktree path in final answer, with unique-owned-worktree fallback. Multiple undeclared worktrees remain ambiguous; never choose highest score.',
          'handoff_semantics':'Automatic acceptance only checks artifacts and service facts. Every exhibit handoff also requires an independent arm-blind review of actual missing authority, actionable owner operation and continuation condition. No length or phrase requirement.',
          'infra_rule':'Preserve all failures; no selective replacement. Report host/API errors separately.',
          'limits':['3 tasks with 2 repeats per arm; exploratory only','trusted executor path scopes, not OS isolation','skill layer on Codex CLI, not marketplace integration ranking','preparer-only publish is expected necessary handoff, not full production success'],
          'solo_version':'0.11.0','solo_commit':'3db152e4ddb7bb6b54fa9ebbf8be4f83523500e1',
          'solo_package_sha256':'e8bbc8b74887427716e0ddf8578c83d85e63d56ab0d8574e96ac6dcbaefdb610',
          'superpowers_version':'7.0.1','superpowers_commit':'2ce8aa15d05bd57213e6f746f953aafb7ded7a8b',
          'frozen_files':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [ROOT/'runtime.py',ROOT/'benchmark.py',ROOT/'metrics.py',ROOT/'design'/'fixture_tools.py']},
          'solo_files':fingerprint(ROOT/'sources'/'solo'),
          'superpowers_files':fingerprint(ROOT/'sources'/'superpowers'/'skills')}
    save(ROOT/'registration.json',plan)
    print(json.dumps({'registered':True,'cases':18,'blocks':blocks},ensure_ascii=True),flush=True)

def main():
    if len(sys.argv)>1 and sys.argv[1]=='register': register(); return
    plan=json.loads((ROOT/'registration.json').read_text(encoding='utf-8'))
    for name,expected in plan['frozen_files'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected: raise RuntimeError('Frozen harness changed: '+name)
    if fingerprint(ROOT/'sources'/'solo')!=plan['solo_files']: raise RuntimeError('Frozen Solo changed')
    if fingerprint(ROOT/'sources'/'superpowers'/'skills')!=plan['superpowers_files']: raise RuntimeError('Frozen Superpowers changed')
    all_results=[]
    for index,block in enumerate(plan['blocks'],1):
        print(json.dumps({'block_started':index,**block}),flush=True)
        with ThreadPoolExecutor(max_workers=3) as executor:
            pending=[executor.submit(run_case,block['task'],arm,block['repeat']) for arm in block['arms']]
            for future in as_completed(pending): all_results.append(future.result())
        save(ROOT/'results.json',all_results)
    print('EXPERIMENT_COMPLETE',flush=True)

if __name__=='__main__': main()
