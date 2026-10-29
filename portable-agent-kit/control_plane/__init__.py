"""Portable autonomous delivery control plane.

The control plane keeps the model-facing Skill thin.  Contracts, leases,
evidence verification and bounded upgrade decisions are persisted and checked
outside the model process.  It deliberately does not grant tools, identities,
or background scheduling to a host.
"""

from .contracts import CONTRACT_FORMAT, canonical_hash, normalize_contract
from .evaluator import EvaluationError, make_verifier, verify_task
from .runtime import ControlPlaneError, DeliveryRuntime
from .adapters import ReceiptError, make_receipt, verify_receipt

__all__ = [
    "CONTRACT_FORMAT",
    "ControlPlaneError",
    "DeliveryRuntime",
    "EvaluationError",
    "canonical_hash",
    "make_verifier",
    "normalize_contract",
    "verify_task",
    "ReceiptError",
    "make_receipt",
    "verify_receipt",
]
