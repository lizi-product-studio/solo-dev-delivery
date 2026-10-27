"""Extract only deduplicated native usage and tool counts; never export messages."""
import argparse
import collections
from datetime import datetime, timezone
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--config', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--cutoff')
args = parser.parse_args()
config = json.loads(args.config.read_text(encoding='utf-8'))
start = config['started_at']
cutoff = args.cutoff or datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
seen_usage, seen_calls = set(), set()
groups = {}
latest = None
for item in config['sources']:
    group = groups.setdefault(item['role'], {'responses': 0, 'tool_calls': 0, 'latest_usage_at': None, 'usage': collections.Counter()})
    for line in Path(item['path']).open(encoding='utf-8'):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        timestamp = row.get('timestamp', '')
        if not start <= timestamp <= cutoff:
            continue
        payload = row.get('payload', {})
        if not isinstance(payload, dict):
            continue
        if row.get('type') == 'token_usage_record':
            identity = (payload.get('thread_id'), payload.get('response_id'))
            if not all(identity) or identity in seen_usage:
                continue
            if item.get('turn_id') and payload.get('turn_id') != item['turn_id']:
                continue
            seen_usage.add(identity)
            group['responses'] += 1
            group['usage'].update(payload['usage'])
            group['latest_usage_at'] = max(group['latest_usage_at'] or timestamp, timestamp)
            latest = max(latest or timestamp, timestamp)
        if row.get('type') == 'response_item' and payload.get('type') in ('function_call', 'custom_tool_call'):
            identity = (item['role'], payload.get('call_id'))
            if identity[1] and identity not in seen_calls:
                seen_calls.add(identity)
                group['tool_calls'] += 1
total = collections.Counter()
for group in groups.values():
    total.update(group['usage'])
    group['usage']['uncached_input_tokens'] = group['usage']['input_tokens'] - group['usage']['cached_input_tokens']
total['uncached_input_tokens'] = total['input_tokens'] - total['cached_input_tokens']
result = {'started_at': start, 'cutoff': cutoff, 'latest_usage_at': latest,
          'wall_seconds': (datetime.fromisoformat(cutoff.replace('Z', '+00:00')) - datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds(),
          'groups': groups, 'usage': total,
          'limits': 'Native token_usage_record only, deduplicated by thread/response. Cached input is inside input; reasoning is inside output. Tool calls count host-visible envelopes, not all nested operations. Running/final responses and log flush delay may be missing. No currency or quota conversion.'}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
