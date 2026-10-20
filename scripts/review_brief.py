#!/usr/bin/env python3
"""Compile relevant acceptance lessons for native or queued review. Read only.

No model calls, queue, tests, deployment, or PASS decision are performed here.
The caller decides whether a task changes an interaction; paths are not authority.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_review_brief(goal, scope, *, ux_required=False, knowledge=None):
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError('需要本次原始目标')
    if not isinstance(scope, list) or not all(isinstance(p, str) and p.strip() for p in scope):
        raise ValueError('范围必须是非空字符串列表')
    if not isinstance(ux_required, bool):
        raise ValueError('需明确是否改变交互')
    selected = ('M01', 'M03') if ux_required else ()
    # Fail visibly rather than silently losing lessons on a broken installation.
    raw = Path(knowledge or ROOT/'knowledge/methods.json').read_bytes() if selected else None
    data = json.loads(raw) if raw is not None else {'methods': []}
    methods = {m['id']: m for m in data['methods']}
    focus, warnings = [], []
    for mid in selected:
        m = methods[mid]
        item = {k: m[k] for k in ('id', 'rule', 'counterexample', 'sources')}
        probes = m.get('review_probes')
        if not probes:
            probes = [m['next_validation']]
            warnings.append(mid+'沿用旧版next_validation；没有新增反例字段，不代表已覆盖全部任务。')
        item['review_probes'] = probes
        focus.append(item)
    return {
        'goal': goal, 'scope': scope, 'ux_required': ux_required,
        'knowledge': {'version': data.get('version'), 'status': data.get('status'),
                      'sha256': hashlib.sha256(raw).hexdigest() if raw is not None else None},
        'experience_warnings': warnings,
        'derive_before_reading_self_tests': '先从原始目标写出本次受影响任务的入口、动作和可见预期，再与实现及旧验收逐项比较；开发清单不能限定审查范围。',
        'focus': focus,
        'record_in_existing_review': '每个关键预期对应候选/环境、实际动作与观察、证据位置；旧证据写明覆盖的动作及复用理由，未测保持未测。无需另建报告或队列。',
        'limits': '这是相关反例的交接材料，不是完整性或通过认证；只检查本次相关路径，不扩展经营授权。无交互任务不强加界面矩阵。',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--path', action='append', default=[])
    parser.add_argument('--ux', action='store_true', help='本次改变交互或做体验整改；纯文案小改不用')
    args = parser.parse_args()
    print(json.dumps(build_review_brief(args.goal, args.path, ux_required=args.ux), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
