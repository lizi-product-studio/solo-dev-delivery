"""Local, subscription-authenticated benchmark runtime. Never publish raw homes/logs."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1] / 'work' / 'solo-dev-delivery-repo'
REAL_HOME = Path('C:/Users/Administrator')
REAL_CODEX = REAL_HOME / '.codex'
CODEX = shutil.which('codex')
PYTHON = shutil.which('python')
CONFIG = tomllib.loads((REAL_CODEX / 'config.toml').read_text(encoding='utf-8'))
MODEL = CONFIG['model']
EFFORT = CONFIG.get('model_reasoning_effort', 'high')
TIER = CONFIG.get('service_tier', 'priority')
NEUTRAL_ROOT = REAL_HOME / '.codex-eval' / 'skill-comparison-20261023'

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')

def fingerprint(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob('*')) if p.is_file() and '.git' not in p.parts}

def setup(label, arm='baseline', builder=None):
    base = NEUTRAL_ROOT / label
    base.mkdir(parents=True, exist_ok=False)
    profile, workspace = base / 'profile', base / 'workspace'
    codex_home = profile / '.codex'
    codex_home.mkdir(parents=True)
    if builder is None:
        workspace.mkdir()
    else:
        builder(workspace)
    # Local-only copy, never included in outputs or source control; deleted after use.
    shutil.copyfile(REAL_CODEX / 'models_cache.json', codex_home / 'models_cache.json')
    # Native discovery through an isolated HOME; disable actual user's Skills explicitly too.
    skills = workspace / '.agents' / 'skills'
    skills.mkdir(parents=True)
    if arm == 'solo':
        shutil.copytree(ROOT / 'sources' / 'solo', skills / 'solo-dev-delivery')
    elif arm == 'superpowers':
        shutil.copytree(ROOT / 'sources' / 'superpowers' / 'skills', skills / 'superpowers')
    elif arm != 'baseline':
        raise ValueError(arm)
    lines = [f'model = {json.dumps(MODEL)}', f'model_reasoning_effort = {json.dumps(EFFORT)}',
             f'service_tier = {json.dumps(TIER)}', 'approval_policy = "never"',
             'sandbox_mode = "danger-full-access"', 'forced_login_method = "chatgpt"',
             'web_search = "disabled"', 'cli_auth_credentials_store = "file"',
             '[features]', 'memories = false', 'plugins = false', 'multi_agent = true',
             '[memories]', 'use_memories = false', 'generate_memories = false',
             '[agents]', 'max_threads = 2', 'max_depth = 1']
    for source in (REAL_HOME / '.agents' / 'skills', REAL_CODEX / 'skills'):
        if source.exists():
            for skill in source.rglob('SKILL.md'):
                for path in (skill.parent, skill):
                    lines += ['[[skills.config]]', 'path = ' + json.dumps(str(path)), 'enabled = false']
    (codex_home / 'config.toml').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP',
        'LANG', 'LC_ALL', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS',
        'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA', 'ALLUSERSPROFILE'}}
    env.update(CODEX_HOME=str(codex_home), USERPROFILE=str(profile), HOME=str(profile),
               APPDATA=str(profile/'AppData'/'Roaming'), LOCALAPPDATA=str(profile/'AppData'/'Local'),
               PYTHONIOENCODING='utf-8', PYTHONUTF8='1', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    subprocess.run(['git', 'init', '--quiet', str(workspace)], check=True, env=env)
    subprocess.run(['git', '-C', str(workspace), 'config', 'user.name', 'Skill Evaluation'], check=True, env=env)
    subprocess.run(['git', '-C', str(workspace), 'config', 'user.email', 'eval@localhost.invalid'], check=True, env=env)
    shutil.copyfile(REAL_CODEX / 'auth.json', codex_home / 'auth.json')
    return {'base':base, 'workspace':workspace, 'home':codex_home, 'profile':profile, 'env':env}

def credential_cleanup(ctx):
    (ctx['home'] / 'auth.json').unlink(missing_ok=True)

def prompt_probe(label='probe-baseline', arm='baseline'):
    ctx = setup(label, arm)
    try:
        command = [CODEX, 'debug', 'prompt-input', 'Report available skills without running any task.']
        cp = subprocess.run(command, cwd=ctx['workspace'], env=ctx['env'], capture_output=True, timeout=90)
        out = ROOT / 'preflight' / label
        out.mkdir(parents=True)
        (out/'prompt.json').write_bytes(cp.stdout)
        (out/'stderr.log').write_bytes(cp.stderr)
        value = {'exit_code':cp.returncode,'stdout_bytes':len(cp.stdout),'stderr_bytes':len(cp.stderr),
                 'config':(ctx['home']/'config.toml').read_text(encoding='utf-8'),
                 'model':MODEL,'effort':EFFORT,'tier':TIER,'base':str(ctx['base'])}
        save(out/'receipt.json',value)
        return value
    finally:
        credential_cleanup(ctx)

if __name__ == '__main__':
    import sys
    result = prompt_probe(*sys.argv[1:])
    print(json.dumps({k:v for k,v in result.items() if k!='config'},ensure_ascii=True))
