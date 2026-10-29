"""Explicit, auditable tool registry for Agent Runtime."""
from __future__ import annotations
import inspect
from copy import deepcopy


class ToolError(ValueError):
    """Trusted adapter rejects before effects; ambiguous execution uses other exceptions."""


class ToolRegistry:
    def __init__(self): self._tools = {};self.execution_context={};self._capture=None
    def begin_capture(self):
        self._capture=[]
    def end_capture(self):
        result=self._capture or [];self._capture=None
        return result
    def register(self, name, fn, *, description="", effects="unknown"):
        if not isinstance(name, str) or not name.strip() or not callable(fn):
            raise ToolError("tool name and callable are required")
        if effects not in ('read','write','unknown'):
            raise ToolError('effects must be read, write or unknown')
        # This is a trusted host declaration, never an argument from a model.
        # Unknown remains conservative for existing/custom adapters.
        self._tools[name] = {"call": fn, "description": description,"effects":effects}
    def call(self, name, arguments):
        self.validate(name,arguments)
        result=self._tools[name]["call"](**arguments)
        if self._capture is not None:
            self._capture.append({'action':'tool','decision':{'name':name,'arguments':deepcopy(arguments)},'result':deepcopy(result)})
        return result
    def validate(self,name,arguments):
        if name not in self._tools: raise ToolError("unknown tool: " + str(name))
        if not isinstance(arguments, dict): raise ToolError("tool arguments must be an object")
        try:
            inspect.signature(self._tools[name]['call']).bind(**arguments)
        except TypeError as exc:
            raise ToolError('invalid tool arguments: '+str(exc)) from exc
    def manifest(self):
        return {name: {'description':row['description'],
                       'arguments':str(inspect.signature(row['call'])),
                       'effects':row['effects']} for name,row in self._tools.items()}
