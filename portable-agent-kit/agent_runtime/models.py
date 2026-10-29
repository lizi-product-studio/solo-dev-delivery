"""Model adapters for Agent Runtime.

The runtime is provider-neutral. OpenAI-compatible providers can be used by
setting AGI_LIKE_MODEL_URL, AGI_LIKE_MODEL, and AGI_LIKE_API_KEY.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


class ModelAdapterError(RuntimeError):
    pass


class CodexDecisionModel:
    """Use the signed-in CLI as a JSON decision model; Runtime owns tools."""
    def __init__(self, workspace, *, executable=None, model=None, log_dir=None, timeout=180):
        from .codex_host import CodexCliAdapter
        self.adapter=CodexCliAdapter(executable)
        self.workspace=workspace; self.model=model; self.log_dir=log_dir; self.timeout=timeout

    def __call__(self, request):
        class NoTools:
            def tool_result(self, *args, **kwargs):
                raise ModelAdapterError('decision model used host tools instead of returning a decision')
        prompt=('You are the JSON decision component of an external Agent Runtime. '
                'Do not use any host tools or delegate. The external runtime will execute your JSON action. '
                'Return one JSON object only, no markdown. Actions: '
                '{"action":"tool","name":"registered tool","arguments":{...}}, '
                '{"action":"skill","name":"registered skill","arguments":{...}}, '
                'or {"action":"finish","answer":"result explanation"}. '
                'Use observations to inspect prior tool results; never infer a file is written without a successful tool result. '
                'Inspect the acceptance conditions before finishing. Request:\n'+json.dumps(request,ensure_ascii=False))
        logs=Path(self.log_dir)/('step-%03d'%request['step']) if self.log_dir else None
        events=self.adapter.run(prompt,workspace=self.workspace,session=NoTools(),model=self.model,
                                timeout=self.timeout,log_dir=logs,sandbox='read-only')
        messages=[e['item']['text'] for e in events if e.get('type')=='item.completed'
                  and (e.get('item') or {}).get('type')=='agent_message']
        if not messages:
            raise ModelAdapterError('Codex returned no decision')
        try:
            value=json.loads(messages[-1])
        except ValueError as exc:
            raise ModelAdapterError('Codex returned invalid decision JSON') from exc
        if not isinstance(value,dict):
            raise ModelAdapterError('Codex decision must be an object')
        return value


class OpenAICompatibleModel:
    def __init__(self, *, url=None, model=None, api_key=None, timeout=90):
        self.url = url or os.environ.get("AGI_LIKE_MODEL_URL")
        self.model = model or os.environ.get("AGI_LIKE_MODEL")
        self.api_key = api_key or os.environ.get("AGI_LIKE_API_KEY")
        self.timeout = timeout
        if not all((self.url, self.model, self.api_key)):
            raise ModelAdapterError("model URL, model and API key are required")

    def __call__(self, request):
        system = (
            "You are the model inside AGI-like Agent Runtime. Return JSON only. "
            "Choose exactly one action: tool, skill, or finish. Never invent tools. "
            "A finish action requires a concise answer."
        )
        payload = {"model": self.model, "temperature": 0,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": json.dumps(request, ensure_ascii=False)}],
                   "response_format": {"type": "json_object"}}
        req = Request(self.url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + self.api_key}, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            result = json.loads(content) if isinstance(content, str) else content
            if not isinstance(result, dict): raise ValueError("model response must be an object")
            return result
        except Exception as exc:
            raise ModelAdapterError(str(exc)) from exc
