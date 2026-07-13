"""
Calero core — the platform-agnostic policy-as-code governance layer.

Import the whole public surface from here:

    from core import PolicyEngine, GovernedClient, Request, Adapter

Platform specifics (which operations exist, how to derive facts from a request)
live in adapters/, not here.
"""

from .adapter import Adapter, NullAdapter
from .governed_client import ApprovalRequired, GovernedClient, PolicyViolation
from .policy_engine import Decision, PolicyEngine, Request, Verdict

__all__ = [
    "Adapter",
    "NullAdapter",
    "PolicyEngine",
    "Request",
    "Decision",
    "Verdict",
    "GovernedClient",
    "PolicyViolation",
    "ApprovalRequired",
]
