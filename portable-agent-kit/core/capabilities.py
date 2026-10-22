"""Stateless capability normalization shared with the persistent core.

Preflight is advisory: no tools, sessions, evidence verdicts, or authorization.
Selection/reporting is adapted from Superpowers Lite; see THIRD_PARTY.md.
"""


def normalize(capabilities):
    if not isinstance(capabilities, dict):
        raise ValueError("capabilities must be an object")
    result = {}
    for name, value in capabilities.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("capability names must be non-empty strings")
        if isinstance(value, bool):
            result[name] = "supported" if value else "unsupported"
        elif value in ("supported", "unsupported", "unknown"):
            result[name] = value
        elif isinstance(value, dict):
            raw = value.get("supported")
            result[name] = "supported" if raw is True else ("unsupported" if raw is False else "unknown")
        else:
            result[name] = "unknown"
    return result


def requirements(contract=None, required=()):
    names = list(required)
    if contract is not None:
        if not isinstance(contract, dict):
            raise ValueError("contract must be an object")
        policy = contract.get("policy", {})
        if not isinstance(policy, dict):
            raise ValueError("policy must be an object")
        for declared in (contract.get("required_capabilities", []), policy.get("required_capabilities", [])):
            if not isinstance(declared, list):
                raise ValueError("required_capabilities must be a list")
            names.extend(declared)
        for flag, capabilities in (
            ("workspace_write", ("fs.read", "fs.write", "shell.exec")),
            ("ux_required", ("browser.open", "browser.control")),
            ("external_actions", ("external.authorized",)),
            ("checkpoint_persist", ("checkpoint.persist",)),
            ("evidence_required", ("evidence.hash",)),
        ):
            value = policy.get(flag, False)
            if not isinstance(value, bool):
                raise ValueError("policy." + flag + " must be boolean")
            if value:
                names.extend(capabilities)
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError("required capabilities must be non-empty strings")
    if not names:
        raise ValueError("declare at least one required capability; no defaults or session are inferred")
    return sorted(set(names))


def preflight(manifest, required=(), contract=None):
    result = {"status": "NEEDS_CONTEXT", "required": [], "supported": [],
              "unsupported": [], "unknown": [], "errors": [],
              "advisory": True, "tools_executed": False, "authorization_granted": False}
    try:
        result["required"] = requirements(contract, required)
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        caps = normalize(manifest.get("capabilities", manifest))
        for state in ("supported", "unsupported", "unknown"):
            result[state] = [name for name in result["required"] if caps.get(name, "unknown") == state]
        result["status"] = "BLOCKED" if result["unsupported"] else ("NEEDS_CONTEXT" if result["unknown"] else "READY")
    except ValueError as error:
        result["errors"].append(str(error))
    return result
