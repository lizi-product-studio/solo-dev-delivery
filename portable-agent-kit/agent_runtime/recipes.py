"""Conservative trace-to-recipe compiler: no code generation or model authority.

Only repeated identical tool topology is compiled. Every observed argument is
an explicit input, never a copied customer value or an implicit new permission.
"""
from copy import deepcopy

FORMAT = 'tool-recipe/1'


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


def execute_recipe(spec, arguments, tools):
    if spec.get('format') != FORMAT:
        raise ValueError('unsupported recipe format')
    parameters = spec.get('parameters')
    steps = spec.get('steps')
    if (not isinstance(parameters, list) or len(set(parameters)) != len(parameters)
            or not all(isinstance(p, str) for p in parameters)
            or not isinstance(arguments, dict) or set(arguments) != set(parameters)):
        raise ValueError('recipe inputs must match the declared parameters exactly')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise ValueError('recipe must have 1..32 tool steps')
    # Validate the whole plan and tool availability before its first side effect.
    manifest = tools.manifest()
    plan = []
    for step in steps:
        if not isinstance(step, dict) or step.get('tool') not in manifest:
            raise ValueError('recipe requires an unavailable tool')
        bindings = step.get('arguments')
        if not isinstance(bindings, dict):
            raise ValueError('invalid recipe argument bindings')
        resolved = {}
        for key, binding in bindings.items():
            if (not isinstance(binding, dict) or set(binding) != {'$input'}
                    or binding['$input'] not in arguments):
                raise ValueError('recipe argument must reference a declared input')
            resolved[key] = deepcopy(arguments[binding['$input']])
        tools.validate(step['tool'],resolved)
        plan.append((step['tool'], resolved))
    observations = []
    for name, resolved in plan:
        result = tools.call(name, resolved)
        observations.append({'tool': name, 'result': result})
        if isinstance(result, dict) and result.get('status') in {'FAILED', 'UNKNOWN'}:
            raise ValueError('recipe tool returned ' + result['status'])
    return observations
