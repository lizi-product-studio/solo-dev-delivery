"""Reference adapter for any host that can pass JSON to a Python process.

It does not execute tools. A real DeepSeek Harness, zcode, KimiCode, or other
host should replace `capabilities()` and `tool()` while keeping the protocol
envelope and the core's gate decisions unchanged.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.state_machine import apply, start, probe, ProtocolError


def capabilities(manifest):
    return manifest.get("capabilities", {})


def handle(message, state=None):
    if message.get("op") == "probe":
        return probe(capabilities(message.get("host", {})))
    if message.get("op") == "start" and "contract" in message:
        if state is not None:
            raise ProtocolError("existing session cannot be reset by a start message")
        return start(message["contract"], capabilities(message.get("host", {})),
                     adapter_id=message.get("host", {}).get("adapter_id", "generic"),
                     host_id=message.get("host", {}).get("host_id", "unknown"))
    if state is None:
        raise ProtocolError("state is required after start")
    return apply(state, message)[0]


if __name__ == "__main__":
    state = None
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            result = handle(message, state)
            # Capability queries are not task state and must not destroy it.
            if message.get("op") != "probe":
                state = result
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"protocol": "aap/0.1", "ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
