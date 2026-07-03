"""
GovernedClient: the ONLY object the agent is given.

It wraps the real Coinbase client and routes every call through the
PolicyEngine first. The agent never holds the raw client or the API key,
so it cannot bypass governance.

Usage:
    from coinbase.rest import RESTClient
    raw = RESTClient(api_key=..., api_secret=...)
    client = GovernedClient(raw, "policy.yaml")

    client.call("get_accounts")                      # -> ALLOW, executes
    client.call("send", {"to": "0xabc..."})          # -> PolicyViolation
"""

from __future__ import annotations

from typing import Any, Optional, Union

from policy_engine import Decision, PolicyEngine, Request, Verdict


class PolicyViolation(Exception):
    def __init__(self, decision: Decision):
        self.decision = decision
        msg = f"{decision.verdict.value}: {decision.reason}"
        if decision.failed_rules:
            msg += f" [rules: {', '.join(decision.failed_rules)}]"
        super().__init__(msg)


class ApprovalRequired(PolicyViolation):
    """Raised when a human approval token is needed. Surface this to a human."""


class GovernedClient:
    def __init__(
        self,
        raw_client: Any,
        policy: Union[str, PolicyEngine],
    ):
        """policy is a path to a policy.yaml or an already-built PolicyEngine
        (useful when the caller needs the same engine to mint approval tokens
        or apply overrides)."""
        self._raw = raw_client
        self.engine = (
            policy if isinstance(policy, PolicyEngine) else PolicyEngine(policy)
        )

    def call(
        self,
        operation: str,
        params: Optional[dict] = None,
        approval_token: Optional[str] = None,
    ) -> Any:
        params = params or {}
        decision = self.engine.evaluate(
            Request(operation=operation, params=params, approval_token=approval_token)
        )

        if decision.verdict is Verdict.NEEDS_APPROVAL:
            raise ApprovalRequired(decision)
        if decision.verdict is Verdict.DENY:
            raise PolicyViolation(decision)

        method = getattr(self._raw, operation, None)
        if method is None:
            raise AttributeError(
                f"Underlying client has no method '{operation}'"
            )
        return method(**params)
