"""
Policy-as-code engine for the Coinbase agent.

Every request the agent wants to make is expressed as a Request and passed to
PolicyEngine.evaluate(). The engine returns a Decision (ALLOW / DENY /
NEEDS_APPROVAL) with a reason, and writes every decision to an append-only
JSONL audit log.

Design principles:
  * Deny by default — an operation must be explicitly allowed.
  * Forbidden list beats allowed list.
  * Business rules are declarative data in policy.yaml, evaluated generically;
    the engine supplies a context document (params + derived facts) and judges
    the request against it. Platform controls (kill switch, allowlist, rate
    limit, active hours) are engine built-ins parameterized by the policy.
  * Fail closed — a rule whose field can't be resolved or whose operator is
    unknown counts as failed.
  * The engine never calls Coinbase itself; it only judges requests.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets as _secrets
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

import yaml


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


@dataclass
class Request:
    operation: str                      # e.g. "get_accounts", "create_order"
    params: dict = field(default_factory=dict)
    approval_token: Optional[str] = None


@dataclass
class Decision:
    verdict: Verdict
    reason: str
    request: Request
    failed_rules: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


# Comparison operators available to rules in policy.yaml.
_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
}

_MISSING = object()


class PolicyEngine:
    def __init__(
        self,
        policy: Union[str, Path, dict],
        approval_secret: Optional[str] = None,
        overrides: Optional[dict] = None,
    ):
        """policy is a path to a YAML file or an already-loaded dict.

        overrides lets callers (demos, tests) tweak the loaded policy without
        writing a modified file: keys replace top-level policy entries, and a
        value of None removes the entry entirely.
        """
        if isinstance(policy, (str, Path)):
            self.policy_path: Optional[Path] = Path(policy)
            self.policy = yaml.safe_load(self.policy_path.read_text())
        else:
            self.policy_path = None
            self.policy = dict(policy)
        for key, value in (overrides or {}).items():
            if value is None:
                self.policy.pop(key, None)
            else:
                self.policy[key] = value

        self._lock = threading.Lock()
        self._request_timestamps: list[dt.datetime] = []   # for rate limiting
        self._daily_notional: dict[str, float] = {}        # date -> USD spent
        self._daily_orders: dict[str, int] = {}            # date -> order count
        self._consumed_nonces: set[str] = set()            # spent approval tokens
        # Secret used to mint/verify human approval tokens.
        self._approval_secret = approval_secret or os.environ.get(
            "AGENT_APPROVAL_SECRET", ""
        )
        audit_cfg = self.policy.get("audit", {})
        log_file = Path(audit_cfg.get("log_file", "audit.log.jsonl"))
        base = self.policy_path.parent if self.policy_path else Path.cwd()
        self.audit_path = log_file if log_file.is_absolute() else base / log_file

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def evaluate(self, request: Request) -> Decision:
        with self._lock:
            decision = self._evaluate_locked(request)
            self._audit(decision)
            return decision

    def mint_approval_token(
        self, operation: str, params: dict, ttl_seconds: Optional[int] = None
    ) -> str:
        """A HUMAN runs this out-of-band to approve one specific request.

        The token is bound to the exact operation and parameters, expires
        after `ttl_seconds`, and is single-use: the engine records the nonce
        once the approved order executes and rejects any replay.
        """
        if not self._approval_secret:
            raise RuntimeError("Set AGENT_APPROVAL_SECRET to mint approval tokens.")
        if ttl_seconds is None:
            ttl_seconds = self.policy.get("approvals", {}).get(
                "token_ttl_seconds", 900
            )
        nonce = _secrets.token_hex(8)
        expires_at = int(dt.datetime.now(dt.timezone.utc).timestamp()) + ttl_seconds
        sig = self._sign(operation, params, nonce, expires_at)
        return f"{nonce}.{expires_at}.{sig}"

    # ------------------------------------------------------------------ #
    #  Rule evaluation
    # ------------------------------------------------------------------ #

    def _evaluate_locked(self, req: Request) -> Decision:
        p = self.policy

        # -- Platform controls ------------------------------------------ #

        # 0. Kill switch & master enable
        ks = p.get("kill_switch_file")
        if ks and Path(ks).exists():
            return Decision(Verdict.DENY, f"kill switch present at {ks}", req)
        if not p.get("enabled", False):
            return Decision(Verdict.DENY, "policy disabled (enabled: false)", req)

        # 1. Forbidden list always wins
        if req.operation in p.get("forbidden_operations", []):
            return Decision(Verdict.DENY, f"'{req.operation}' is forbidden", req)

        # 2. Deny-by-default allowlist
        if req.operation not in p.get("allowed_operations", []):
            return Decision(
                Verdict.DENY, f"'{req.operation}' not in allowed_operations", req
            )

        # 3. Active hours
        hours = p.get("active_hours_utc")
        if hours:
            now_h = dt.datetime.now(dt.timezone.utc).hour
            if not (hours["start"] <= now_h < hours["end"]):
                return Decision(
                    Verdict.DENY,
                    f"outside active hours {hours['start']}:00-{hours['end']}:00 UTC",
                    req,
                )

        # 4. Rate limit
        limit = p.get("rate_limit", {}).get("max_requests_per_minute")
        if limit:
            now = dt.datetime.now(dt.timezone.utc)
            cutoff = now - dt.timedelta(minutes=1)
            self._request_timestamps = [
                t for t in self._request_timestamps if t > cutoff
            ]
            if len(self._request_timestamps) >= limit:
                return Decision(
                    Verdict.DENY, f"rate limit {limit}/min exceeded", req
                )
            self._request_timestamps.append(now)

        # -- Business rules (declarative, from policy.yaml) -------------- #

        context = self._build_context(req)
        failures: list[tuple[dict, str]] = []   # (rule, effect)
        for rule in p.get("rules", []):
            if not self._rule_applies(rule, req.operation):
                continue
            if not self._check_passes(rule.get("check", {}), context):
                failures.append((rule, rule.get("on_fail", "deny")))

        denies = [r for r, effect in failures if effect != "needs_approval"]
        approvals = [r for r, effect in failures if effect == "needs_approval"]

        if denies:
            ids = [r.get("id", "?") for r, _ in failures]
            first = denies[0]
            return Decision(
                Verdict.DENY,
                f"rule '{first.get('id')}' failed: "
                f"{first.get('description', 'no description')}",
                req,
                failed_rules=ids,
            )

        consumed_nonce: Optional[str] = None
        if approvals:
            ids = [r.get("id", "?") for r in approvals]
            ok, why, nonce = self._verify_approval_token(req)
            if not ok:
                verdict = (
                    Verdict.DENY
                    if why in ("invalid signature", "already used")
                    else Verdict.NEEDS_APPROVAL
                )
                first = approvals[0]
                return Decision(
                    verdict,
                    f"rule '{first.get('id')}' requires human approval "
                    f"({first.get('description', '')}): approval token {why}",
                    req,
                    failed_rules=ids,
                )
            consumed_nonce = nonce

        # Request passes: commit state so subsequent evaluations see it.
        if req.operation == "create_order":
            today = self._today()
            notional = context["derived"].get("notional_usd") or 0.0
            self._daily_notional[today] = (
                self._daily_notional.get(today, 0.0) + notional
            )
            self._daily_orders[today] = self._daily_orders.get(today, 0) + 1
        if consumed_nonce:
            self._consumed_nonces.add(consumed_nonce)

        return Decision(Verdict.ALLOW, "all checks passed", req)

    # ------------------------------------------------------------------ #
    #  Context document
    # ------------------------------------------------------------------ #

    def _build_context(self, req: Request) -> dict:
        """Assemble the facts rules are judged against.

        `params` is the request as submitted; `derived` holds facts the
        engine computes (parsed notional, running daily totals). Rules
        reference these with dotted paths like `derived.notional_usd`.
        """
        derived: dict[str, Any] = {}
        if req.operation == "create_order":
            derived["side"] = str(req.params.get("side", "")).upper()
            try:
                derived["notional_usd"] = float(
                    req.params.get("quote_size", req.params.get("notional_usd"))
                )
            except (TypeError, ValueError):
                derived["notional_usd"] = None   # unresolvable -> rules fail closed
            today = self._today()
            spent = self._daily_notional.get(today, 0.0)
            if derived["notional_usd"] is not None:
                derived["daily_notional_after"] = spent + derived["notional_usd"]
            derived["daily_order_count"] = self._daily_orders.get(today, 0)
        return {"params": dict(req.params), "derived": derived}

    @staticmethod
    def _rule_applies(rule: dict, operation: str) -> bool:
        applies = rule.get("applies_to", "*")
        if isinstance(applies, str):
            return applies in ("*", operation)
        return operation in applies

    @staticmethod
    def _check_passes(check: dict, context: dict) -> bool:
        op = _OPS.get(check.get("op"))
        if op is None:
            return False   # unknown operator -> fail closed
        node: Any = context
        for part in str(check.get("field", "")).split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                node = _MISSING
                break
        value = node
        if value is _MISSING or value is None:
            return False   # unresolvable field -> fail closed
        try:
            return bool(op(value, check.get("value")))
        except TypeError:
            return False   # incomparable types -> fail closed

    # ------------------------------------------------------------------ #
    #  Approval tokens
    # ------------------------------------------------------------------ #

    def _verify_approval_token(
        self, req: Request
    ) -> tuple[bool, str, Optional[str]]:
        """Returns (ok, failure reason, nonce)."""
        if not req.approval_token:
            return False, "missing", None
        try:
            nonce, expires_str, sig = req.approval_token.split(".")
            expires_at = int(expires_str)
        except ValueError:
            return False, "invalid signature", None
        if not self._approval_secret:
            return False, "missing", None   # can't verify without the secret
        expected = self._sign(req.operation, req.params, nonce, expires_at)
        if not hmac.compare_digest(sig, expected):
            return False, "invalid signature", None
        if nonce in self._consumed_nonces:
            return False, "already used", None
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        if now > expires_at:
            return False, "expired", None
        return True, "", nonce

    def _sign(
        self, operation: str, params: dict, nonce: str, expires_at: int
    ) -> str:
        payload = json.dumps(
            {"op": operation, "params": params, "nonce": nonce, "exp": expires_at},
            sort_keys=True,
        )
        return hmac.new(
            self._approval_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _today() -> str:
        return dt.datetime.now(dt.timezone.utc).date().isoformat()

    def _audit(self, d: Decision) -> None:
        entry = {
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "agent_id": self.policy.get("agent_id"),
            "operation": d.request.operation,
            "params": d.request.params,
            "verdict": d.verdict.value,
            "reason": d.reason,
            "failed_rules": d.failed_rules,
        }
        with open(self.audit_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
