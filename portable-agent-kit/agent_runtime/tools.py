"""Explicit, auditable tool registry for Agent Runtime."""
from __future__ import annotations
import inspect


class ToolError(ValueError):
    """Trusted adapter rejects before effects; ambiguous execution uses other exceptions."""


class ToolRegistry:
    def __init__(self): self._tools = {}
    def register(self, name, fn, *, description=""):
        if not isinstance(name, str) or not name.strip() or not callable(fn):
            raise ToolError("tool name and callable are required")
        self._tools[name] = {"call": fn, "description": description}
    def call(self, name, arguments):
        self.validate(name,arguments)
        return self._tools[name]["call"](**arguments)
    def validate(self,name,arguments):
        if name not in self._tools: raise ToolError("unknown tool: " + str(name))
        if not isinstance(arguments, dict): raise ToolError("tool arguments must be an object")
        try:
            inspect.signature(self._tools[name]['call']).bind(**arguments)
        except TypeError as exc:
            raise ToolError('invalid tool arguments: '+str(exc)) from exc
    def manifest(self):
        return {name: {'description':row['description'],
                       'arguments':str(inspect.signature(row['call']))} for name,row in self._tools.items()}
