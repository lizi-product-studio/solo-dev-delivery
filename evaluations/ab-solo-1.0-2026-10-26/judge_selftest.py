import json, pathlib, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).parent
JUDGE=ROOT/'judge.py'
TASKS=ROOT/'tasks'
with tempfile.TemporaryDirectory() as t:
    root=pathlib.Path(t)
    task='01_normalize_email'; ws=root/'workspace'; ws.mkdir()
    for name in ('TASK.md','CONTRACT.md','inputs.json'):
        (ws/name).write_bytes((TASKS/task/name).read_bytes())
    (ws/'src.py').write_text('def solve(value): return value.strip().lower()\n',encoding='utf-8')
    out=root/'result.json'
    p=subprocess.run([sys.executable,str(JUDGE),task,str(ws),'--output',str(out)],capture_output=True,text=True)
    assert p.returncode==0, p.stdout+p.stderr
    assert json.loads(out.read_text())['task_success']
    (ws/'src.py').write_text('def solve(value): return value\n',encoding='utf-8')
    p=subprocess.run([sys.executable,str(JUDGE),task,str(ws),'--output',str(out)],capture_output=True,text=True)
    assert p.returncode==1
    assert not json.loads(out.read_text())['task_success']
print('judge_selftest: PASS')
