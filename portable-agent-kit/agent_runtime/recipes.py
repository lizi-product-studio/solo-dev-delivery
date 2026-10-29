"""Conservative trace-to-recipe compiler: no code generation or model authority.

Only repeated identical tool topology is compiled. Every observed argument is
an explicit input, never a copied customer value or an implicit new permission.
"""
from copy import deepcopy
from .tools import ToolError

FORMAT = 'tool-recipe/1'


class SkillPreflightError(ToolError):
    """Trusted recipe rejection before any possible write-capable step ran."""


def compile_traces(traces):
    if len(traces) < 2:
        return None
    sequences = []
    for events in traces:
        sequence = []
        if not isinstance(events, list):
            return None
        for event in events:
            if not isinstance(event,dict):
                return None
            if event.get('action') not in {'tool', 'finish'}:
                return None
            if event.get('action') == 'finish':
                continue
            decision = event.get('decision') or {}
            name, arguments = decision.get('name'), decision.get('arguments')
            if not isinstance(name, str) or not name or not isinstance(arguments, dict):
                return None
            if 'result' not in event:
                return None
            result = event['result']
            if isinstance(result, dict) and result.get('status') in {'FAILED', 'UNKNOWN'}:
                return None
            if any(not isinstance(key, str) for key in arguments):
                return None
            sequence.append((name, sorted(arguments)))
        if not sequence or len(sequence) > 32:
            return None
        sequences.append(sequence)
    if any(sequence != sequences[0] for sequence in sequences[1:]):
        return None
    parameters, steps = [], []
    for index, (name, keys) in enumerate(sequences[0], 1):
        bindings = {}
        for key in keys:
            parameter = 'step%d_%s' % (index, key)
            parameters.append(parameter)
            bindings[key] = {'$input': parameter}
        steps.append({'tool': name, 'arguments': bindings})
    return {'format': FORMAT, 'parameters': parameters, 'steps': steps}


def recipe_procedure(spec):
    """Expose topology/input bindings only, never literals or training data."""
    if not isinstance(spec,dict) or spec.get('format')!=FORMAT:return []
    parameters=spec.get('parameters');steps=spec.get('steps')
    if not isinstance(parameters,list) or not all(isinstance(p,str) for p in parameters):return []
    if not isinstance(steps,list) or not 1<=len(steps)<=32:return []
    procedure=[]
    for step in steps:
        if not isinstance(step,dict) or not isinstance(step.get('tool'),str):return []
        arguments=step.get('arguments')
        if not isinstance(arguments,dict):return []
        projection={}
        for key,binding in arguments.items():
            if (not isinstance(key,str) or not isinstance(binding,dict) or set(binding)!={'$input'}
                    or not isinstance(binding['$input'],str) or binding['$input'] not in parameters):return []
            projection[key]={'$input':binding['$input']}
        procedure.append({'tool':step['tool'],'arguments':projection})
    return procedure


def _recipe_plan(spec,arguments,tools):
    if not isinstance(spec,dict) or spec.get('format') != FORMAT:
        raise SkillPreflightError('unsupported recipe format')
    parameters = spec.get('parameters')
    steps = spec.get('steps')
    if (not isinstance(parameters, list) or not all(isinstance(p, str) for p in parameters)
            or len(set(parameters)) != len(parameters)
            or not isinstance(arguments, dict) or set(arguments) != set(parameters)):
        raise SkillPreflightError('recipe inputs must match the declared parameters exactly')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise SkillPreflightError('recipe must have 1..32 tool steps')
    # Validate the whole plan and tool availability before its first side effect.
    manifest = tools.manifest()
    plan = []
    for step in steps:
        if (not isinstance(step, dict) or not isinstance(step.get('tool'),str)
                or step['tool'] not in manifest):
            raise SkillPreflightError('recipe requires an unavailable tool')
        bindings = step.get('arguments')
        if not isinstance(bindings, dict):
            raise SkillPreflightError('invalid recipe argument bindings')
        resolved = {}
        for key, binding in bindings.items():
            if (not isinstance(binding, dict) or set(binding) != {'$input'}
                    or not isinstance(binding['$input'],str) or binding['$input'] not in arguments):
                raise SkillPreflightError('recipe argument must reference a declared input')
            resolved[key] = deepcopy(arguments[binding['$input']])
        try:tools.validate(step['tool'],resolved)
        except ToolError as exc:raise SkillPreflightError(str(exc)) from exc
        plan.append((step['tool'], resolved))
    return plan,manifest


def execute_recipe(spec, arguments, tools):
    plan,manifest=_recipe_plan(spec,arguments,tools)
    observations = []
    possible_effect=False
    for name, resolved in plan:
        try:result = tools.call(name, resolved)
        except ToolError as exc:
            if not possible_effect:raise SkillPreflightError(str(exc)) from exc
            # Do not leak a nested preflight subtype after an earlier step may
            # have written. The outer recipe outcome must stay unknown.
            raise ToolError(str(exc)) from exc
        if manifest[name].get('effects','unknown')!='read':possible_effect=True
        observations.append({'tool': name, 'result': result})
        if isinstance(result, dict) and result.get('status') in {'FAILED', 'UNKNOWN'}:
            raise ValueError('recipe tool returned ' + result['status'])
    return observations
