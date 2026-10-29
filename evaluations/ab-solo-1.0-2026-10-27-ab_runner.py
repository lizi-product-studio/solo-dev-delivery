from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
TASKS = ROOT / 'ab-solo-1.0-2026-10-26' / 'tasks'
JUDGE = ROOT / 'ab-solo-1.0-2026-10-26' / 'judge.py'
SKILL_SOURCE = Path(r'C:\Users\Administrator\Documents\Codex\solo-dev-delivery-1.0-rebuild-v2\solo-dev-delivery')
PACKAGE = REPO / 'packages' / 'solo-dev-delivery-1.0.0.zip'
MODEL = 'gpt-6.1-sol'
EFFORT = 'medium'
TIMEOUT_SECONDS = 300
CLI = shutil.which('codex') or 'codex'
RAW_ROOT = Path(os.environ.get('AB_RAW_ROOT', str(REPO.parent / 'ab-solo-1.0-2026-10-27-raw'))).resolve()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def clean_env(profile: Path, codex_home: Path) -> dict[str, str]:
    allowed = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','PROCESSOR_ARCHITECTURE',
               'PROCESSOR_IDENTIFIER','NUMBER_OF_PROCESSORS','PROGRAMDATA','PROGRAMFILES','PROGRAMFILES(X86)',
               'COMMONPROGRAMFILES','COMMONPROGRAMFILES(X86)','SYSTEMDRIVE','OS'}
    env = {k:v for k,v in os.environ.items() if k.upper() in allowed}
    env.update({
        'CODEX_HOME': str(codex_home), 'USERPROFILE': str(profile), 'HOME': str(profile),
        'APPDATA': str(profile/'AppData/Roaming'), 'LOCALAPPDATA': str(profile/'AppData/Local'),
        'PYTHONUTF8':'1', 'PYTHONIOENCODING':'utf-8', 'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_CONFIG_NOSYSTEM':'1', 'GIT_TERMINAL_PROMPT':'0', 'GCM_INTERACTIVE':'never',
    })
    return env


def ancestor_injections(path: Path) -> list[str]:
    found=[]
    for parent in [path, *path.parents]:
        for name in ('AGENTS.md','.agents','.codex','skills','plugins','memories'):
            target=parent/name
            if target.exists(): found.append(str(target))
    return found


def copy_task(task: str, workspace: Path) -> None:
    source = TASKS/task
    for item in source.iterdir():
        if item.name in {'.git'}: continue
        target=workspace/item.name
        if item.is_dir(): shutil.copytree(item,target)
        else: shutil.copy2(item,target)


def git_init(workspace: Path, env: dict[str,str]) -> None:
    subprocess.run(['git','init','--quiet',str(workspace)],check=True,env=env)
    subprocess.run(['git','-C',str(workspace),'config','user.name','AB Pilot'],check=True,env=env)
    subprocess.run(['git','-C',str(workspace),'config','user.email','ab-pilot@localhost.invalid'],check=True,env=env)
    subprocess.run(['git','-C',str(workspace),'add','.'],check=True,env=env,capture_output=True)
    subprocess.run(['git','-C',str(workspace),'commit','--quiet','-m','Frozen public task'],check=True,env=env,capture_output=True)


def config_summary() -> dict:
    return {'model':MODEL,'reasoning_effort':EFFORT,'timeout_seconds':TIMEOUT_SECONDS,
            'approval_policy':'never','sandbox':'dangerously-bypass-approvals-and-sandbox','service_tier':'default',
            'memories':False,'plugins':False,'multi_agent':False,'network':'not used by prompt; host sandbox controls access'}


def session_files(codex_home: Path) -> list[Path]:
    return sorted(codex_home.rglob('*.jsonl'))


def extract_usage(files: list[Path]) -> dict:
    records=[]; counts=[]
    for path in files:
        for line_no,line in enumerate(path.read_text(encoding='utf-8',errors='replace').splitlines(),1):
            try: value=json.loads(line)
            except json.JSONDecodeError: continue
            if not isinstance(value,dict): continue
            typ=value.get('type'); payload=value.get('payload') if isinstance(value.get('payload'),dict) else {}
            if typ=='token_usage_record':
                records.append({'file':str(path),'line':line_no,'payload':payload})
            if typ=='event_msg' and payload.get('type')=='token_count':
                counts.append({'file':str(path),'line':line_no,'payload':payload})
    latest={}
    for rec in records:
        payload=rec['payload']
        for key in ('usage','turn_token_usage','thread_token_usage'):
            if isinstance(payload.get(key),dict): latest[key]=payload[key]
    usage=latest.get('thread_token_usage') or latest.get('usage') or latest.get('turn_token_usage')
    fields=('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')
    parsed={k:usage.get(k) for k in fields} if isinstance(usage,dict) else None
    return {'usage_schema':'token_usage_record.thread_token_usage' if latest.get('thread_token_usage') else ('token_usage_record.usage' if latest.get('usage') else None),
            'usage':parsed,'records':len(records),'token_count_events':len(counts),
            'unavailable_reason':None if parsed else 'no token_usage_record with usage fields in disposable session logs'}


def safe_kill(proc: subprocess.Popen) -> dict:
    result={'method':'taskkill-tree','pid':proc.pid,'status':'UNKNOWN'}
    command=['taskkill','/PID',str(proc.pid),'/T','/F'] if os.name=='nt' else ['kill','-TERM',str(proc.pid)]
    try:
        done=subprocess.run(command,capture_output=True,timeout=15)
        result['exit_code']=done.returncode
        result['stderr']=done.stderr.decode('utf-8','replace')[-2000:]
        result['status']='COMPLETE' if done.returncode==0 else 'FAILED'
    except Exception as exc:
        result['status']='FAILED'; result['error']=repr(exc)
    try: proc.wait(timeout=15)
    except Exception as exc: result['wait_error']=repr(exc)
    return result


def run_trial(task: str, arm: str, repeat: int, output_root: Path) -> dict:
    label=f'{task}__{arm}__r{repeat}'
    run=output_root/label; run.mkdir(parents=True,exist_ok=False)
    profile=run/'profile'; home=profile/'.codex'; workspace=run/'workspace'; skill_root=workspace/'.agents/skills'
    home.mkdir(parents=True); workspace.mkdir(parents=True)
    copy_task(task,workspace)
    if arm=='solo-1.0':
        shutil.copytree(SKILL_SOURCE,skill_root/'solo-dev-delivery')
    env=clean_env(profile,home)
    git_init(workspace,env)
    if (Path.home()/'.codex/auth.json').is_file(): shutil.copy2(Path.home()/'.codex/auth.json',home/'auth.json')
    if (Path.home()/'.codex/models_cache.json').is_file(): shutil.copy2(Path.home()/'.codex/models_cache.json',home/'models_cache.json')
    config={'config':config_summary(),'arm':arm,'task_id':task,'repeat':repeat,
            'skill_available':(arm=='solo-1.0'),'skill_source_sha256':sha(SKILL_SOURCE/'SKILL.md') if arm=='solo-1.0' else None,
            'package_sha256':sha(PACKAGE),'cli_path':CLI,'cli_version':subprocess.run([CLI,'--version'],capture_output=True,text=True,env=env).stdout.strip(),
            'ancestor_injections':ancestor_injections(workspace),'workspace':str(workspace),'started_at':now()}
    write_json(run/'trial-config.json',config)
    prompt=(
        '在当前项目中完成 TASK.md 的任务。先阅读 TASK.md 和 CONTRACT.md，检查现有 src.py，'
        '只修改 src.py；不要修改 TASK.md、CONTRACT.md、inputs.json，也不要访问网络、真实账号或项目外文件。'
        '完成后运行你认为必要的本地测试，确认输入未被修改，并用简短文字说明改动和验证结果。'
    )
    if arm=='solo-1.0':
        prompt='当前项目已安装 solo-dev-delivery 1.0.0，请按该 Skill 的适用规则推进本任务；'+prompt
    (run/'prompt.txt').write_text(prompt,encoding='utf-8')
    events=run/'events.jsonl'; stderr=run/'stderr.log'; final=run/'final.txt'
    start=time.monotonic(); started=now(); timed_out=False; kill=None
    args=[CLI,'exec','--ignore-user-config','--ignore-rules','--json','--model',MODEL,'-c',f'model_reasoning_effort="{EFFORT}"','-c','service_tier="default"','-c','approval_policy="never"','--dangerously-bypass-approvals-and-sandbox','-C',str(workspace),'-o',str(final),'-']
    with events.open('wb') as out, stderr.open('wb') as err:
        proc=subprocess.Popen(args,cwd=workspace,env=env,stdin=subprocess.PIPE,stdout=out,stderr=err)
        try: proc.communicate(prompt.encode('utf-8'),timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out=True; kill=safe_kill(proc)
    elapsed=round(time.monotonic()-start,3); ended=now()
    sessions=session_files(home)
    session_dir=run/'sessions'; session_dir.mkdir()
    copied=[]
    for source in sessions:
        target=session_dir/source.name
        shutil.copy2(source,target); copied.append(target)
    usage=extract_usage(copied)
    event_rows=[]; errors=[]; intervention=0
    for line in events.read_text(encoding='utf-8',errors='replace').splitlines():
        try: value=json.loads(line); event_rows.append(value)
        except json.JSONDecodeError: continue
        if isinstance(value,dict):
            if value.get('type') in {'error','turn.failed'}: errors.append(value)
            if value.get('type','').lower() in {'approval_requested','request_user_input','user_input'}: intervention+=1
    evaluation_path=run/'evaluation.json'
    judge=subprocess.run([sys.executable,str(JUDGE),task,str(workspace),'--output',str(evaluation_path)],capture_output=True,text=True,encoding='utf-8',env=env)
    try: evaluation=json.loads(evaluation_path.read_text(encoding='utf-8'))
    except Exception: evaluation={'task_id':task,'task_success':False,'judge_error':judge.stderr[-2000:]}
    failure_class=None
    if timed_out: failure_class='timeout'
    elif errors:
        text=json.dumps(errors,ensure_ascii=False)
        failure_class='capacity_error' if 'capacity' in text.lower() else ('policy_blocked' if 'blocked by policy' in text.lower() or 'read-only sandbox' in text.lower() or '无法启动任何本地 shell' in text else 'codex_error')
    elif judge.returncode!=0: failure_class='task_failed'
    status='completed' if failure_class is None and evaluation.get('task_success') else ('censored' if failure_class in {'timeout','capacity_error','codex_error'} else 'failed')
    receipt={'schema_version':'ab-1.0','trial_id':label,'task_id':task,'arm':arm,'repeat':repeat,'status':status,
             'model':MODEL,'reasoning_effort':EFFORT,'timeout_seconds':TIMEOUT_SECONDS,'started_at':started,'ended_at':ended,'elapsed_seconds':elapsed,
             'exit_code':proc.returncode,'timed_out':timed_out,'process_cleanup':kill,'execution_errors':errors,
             'failure_class':failure_class,'capacity_failure':failure_class=='capacity_error','user_intervention_count':intervention,
             'user_intervention_status':'observed_zero_noninteractive' if intervention==0 else 'observed_events',
             'evaluation':evaluation,'usage':usage,'raw':{'events':str(events),'stderr':str(stderr),'final':str(final),'sessions':str(session_dir)},
             'workspace':str(workspace),'skill_available':arm=='solo-1.0','skill_sha256':config['skill_source_sha256']}
    write_json(run/'receipt.json',receipt)
    config['ended_at']=ended; config['elapsed_seconds']=elapsed; config['exit_code']=proc.returncode
    write_json(run/'trial-config.json',config)
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument('--task',required=True); parser.add_argument('--arm',choices=['baseline','solo-1.0'],required=True); parser.add_argument('--repeat',type=int,required=True); parser.add_argument('--output',type=Path,default=RAW_ROOT)
    args=parser.parse_args(argv); args.output.mkdir(parents=True,exist_ok=True)
    try: result=run_trial(args.task,args.arm,args.repeat,args.output); print(json.dumps(result,ensure_ascii=False)); return 0 if result['status']=='completed' else 1
    except Exception as exc:
        print(json.dumps({'status':'harness_error','task_id':args.task,'arm':args.arm,'repeat':args.repeat,'error':repr(exc)},ensure_ascii=False),file=sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
