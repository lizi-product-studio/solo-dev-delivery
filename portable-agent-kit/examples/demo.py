"""Local demo: wrong artifact blocks; repair plus actual readback passes."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    work = root / 'work'
    work.mkdir()
    def run(*args):
        proc = subprocess.run([sys.executable, str(KIT / 'scripts/session.py'), *map(str, args),
                               '--session', str(root / 'control')], text=True, capture_output=True, check=True)
        result = json.loads(proc.stdout)
        print(json.dumps(result, ensure_ascii=False))
        return result
    run('init', '--workspace', work, '--contract', KIT / 'examples/contracts/software.json')
    (work / 'config.json').write_text('{"retry":{"attempts":99,"unknown_result":"blind_retry"}}')
    assert run('check', '--check', 'AC1')['verdict'] == 'FAIL'
    assert run('finish')['status'] != 'COMPLETED'
    (work / 'config.json').write_text('{"retry":{"attempts":2,"unknown_result":"reconcile"}}')
    for check in ('AC1', 'AC2'):
        assert run('check', '--check', check)['verdict'] == 'PASS'
    assert run('finish')['status'] == 'COMPLETED'
