"""Unit tests for the policy engine — the "policies are testable" half of
the policy-as-code pitch. Every rule's verdict is asserted here, so a policy
change that weakens a guardrail fails CI before it reaches the agent."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from policy_engine import PolicyEngine, Request, Verdict  # noqa: E402

SECRET = "test-secret"


def base_policy(tmp_path):
    """A self-contained policy dict mirroring policy.yaml, minus active
    hours (so tests are time-independent) and with tmp paths."""
    return {
        "version": 2,
        "agent_id": "test-agent",
        "enabled": True,
        "kill_switch_file": str(tmp_path / "KILL"),
        "allowed_operations": ["get_accounts", "create_order"],
        "forbidden_operations": ["send", "withdraw"],
        "rules": [
            {
                "id": "allowed-products",
                "applies_to": "create_order",
                "description": "Only trade approved pairs",
                "check": {
                    "field": "params.product_id",
                    "op": "in",
                    "value": ["BTC-USD", "ETH-USD"],
                },
                "on_fail": "deny",
            },
            {
                "id": "buy-only",
                "applies_to": "create_order",
                "description": "Accumulate-only agent",
                "check": {"field": "derived.side", "op": "in", "value": ["BUY"]},
                "on_fail": "deny",
            },
            {
                "id": "positive-notional",
                "applies_to": "create_order",
                "description": "Order size must be positive",
                "check": {"field": "derived.notional_usd", "op": ">", "value": 0},
                "on_fail": "deny",
            },
            {
                "id": "per-order-cap",
                "applies_to": "create_order",
                "description": "No single order above $25",
                "check": {
                    "field": "derived.notional_usd",
                    "op": "<=",
                    "value": 25.00,
                },
                "on_fail": "deny",
            },
            {
                "id": "daily-notional-cap",
                "applies_to": "create_order",
                "description": "Daily spend under $100",
                "check": {
                    "field": "derived.daily_notional_after",
                    "op": "<=",
                    "value": 100.00,
                },
                "on_fail": "deny",
            },
            {
                "id": "daily-order-count",
                "applies_to": "create_order",
                "description": "At most 3 orders per day",
                "check": {
                    "field": "derived.daily_order_count",
                    "op": "<",
                    "value": 3,
                },
                "on_fail": "deny",
            },
            {
                "id": "human-approval-threshold",
                "applies_to": "create_order",
                "description": "Orders above $10 need approval",
                "check": {
                    "field": "derived.notional_usd",
                    "op": "<=",
                    "value": 10.00,
                },
                "on_fail": "needs_approval",
            },
        ],
        "approvals": {"token_ttl_seconds": 900},
        "rate_limit": {"max_requests_per_minute": 1000},
        "audit": {"log_file": str(tmp_path / "audit.jsonl")},
    }


@pytest.fixture
def engine(tmp_path):
    return PolicyEngine(base_policy(tmp_path), approval_secret=SECRET)


def order(quote_size, product="BTC-USD", side="BUY", token=None):
    return Request(
        "create_order",
        {"product_id": product, "side": side, "quote_size": str(quote_size)},
        approval_token=token,
    )


# --------------------------------------------------------------------- #
#  Platform controls
# --------------------------------------------------------------------- #

def test_allowed_read_op(engine):
    assert engine.evaluate(Request("get_accounts")).verdict is Verdict.ALLOW


def test_forbidden_op_denied(engine):
    d = engine.evaluate(Request("send", {"to": "0xabc"}))
    assert d.verdict is Verdict.DENY
    assert "forbidden" in d.reason


def test_unknown_op_denied_by_default(engine):
    d = engine.evaluate(Request("delete_everything"))
    assert d.verdict is Verdict.DENY
    assert "allowed_operations" in d.reason


def test_forbidden_beats_allowed(tmp_path):
    policy = base_policy(tmp_path)
    policy["allowed_operations"].append("send")
    eng = PolicyEngine(policy, approval_secret=SECRET)
    assert eng.evaluate(Request("send")).verdict is Verdict.DENY


def test_kill_switch(engine, tmp_path):
    (tmp_path / "KILL").touch()
    d = engine.evaluate(Request("get_accounts"))
    assert d.verdict is Verdict.DENY
    assert "kill switch" in d.reason


def test_disabled_policy(tmp_path):
    eng = PolicyEngine(
        base_policy(tmp_path), approval_secret=SECRET, overrides={"enabled": False}
    )
    assert eng.evaluate(Request("get_accounts")).verdict is Verdict.DENY


def test_rate_limit(tmp_path):
    eng = PolicyEngine(
        base_policy(tmp_path),
        approval_secret=SECRET,
        overrides={"rate_limit": {"max_requests_per_minute": 3}},
    )
    for _ in range(3):
        assert eng.evaluate(Request("get_accounts")).verdict is Verdict.ALLOW
    d = eng.evaluate(Request("get_accounts"))
    assert d.verdict is Verdict.DENY
    assert "rate limit" in d.reason


# --------------------------------------------------------------------- #
#  Business rules
# --------------------------------------------------------------------- #

def test_small_buy_allowed(engine):
    assert engine.evaluate(order(5)).verdict is Verdict.ALLOW


def test_product_not_allowed(engine):
    d = engine.evaluate(order(5, product="DOGE-USD"))
    assert d.verdict is Verdict.DENY
    assert "allowed-products" in d.failed_rules


def test_sell_denied(engine):
    d = engine.evaluate(order(5, side="SELL"))
    assert d.verdict is Verdict.DENY
    assert "buy-only" in d.failed_rules


def test_unparseable_notional_fails_closed(engine):
    d = engine.evaluate(
        Request(
            "create_order",
            {"product_id": "BTC-USD", "side": "BUY", "quote_size": "lots"},
        )
    )
    assert d.verdict is Verdict.DENY
    assert "positive-notional" in d.failed_rules


def test_per_order_cap(engine):
    d = engine.evaluate(order(50))
    assert d.verdict is Verdict.DENY
    assert "per-order-cap" in d.failed_rules


def test_daily_notional_cap(tmp_path):
    policy = base_policy(tmp_path)
    for rule in policy["rules"]:
        if rule["id"] == "daily-notional-cap":
            rule["check"]["value"] = 15.00
    eng = PolicyEngine(policy, approval_secret=SECRET)
    assert eng.evaluate(order(10)).verdict is Verdict.ALLOW
    d = eng.evaluate(order(10))   # would push daily total to $20 > $15
    assert d.verdict is Verdict.DENY
    assert "daily-notional-cap" in d.failed_rules


def test_daily_order_count(engine):
    for _ in range(3):
        assert engine.evaluate(order(5)).verdict is Verdict.ALLOW
    d = engine.evaluate(order(5))
    assert d.verdict is Verdict.DENY
    assert "daily-order-count" in d.failed_rules


def test_denied_order_does_not_consume_budget(engine):
    engine.evaluate(order(50))   # denied by per-order cap
    ctx = engine._build_context(order(5))
    assert ctx["derived"]["daily_notional_after"] == 5.0


# --------------------------------------------------------------------- #
#  Human approval flow
# --------------------------------------------------------------------- #

def test_over_threshold_needs_approval(engine):
    d = engine.evaluate(order(15))
    assert d.verdict is Verdict.NEEDS_APPROVAL
    assert "human-approval-threshold" in d.failed_rules


def test_valid_token_allows(engine):
    req = order(15)
    token = engine.mint_approval_token(req.operation, req.params)
    d = engine.evaluate(order(15, token=token))
    assert d.verdict is Verdict.ALLOW


def test_token_bound_to_exact_params(engine):
    req = order(15)
    token = engine.mint_approval_token(req.operation, req.params)
    d = engine.evaluate(order(20, token=token))   # different amount
    assert d.verdict is Verdict.DENY
    assert "invalid signature" in d.reason


def test_token_single_use(engine):
    req = order(15)
    token = engine.mint_approval_token(req.operation, req.params)
    assert engine.evaluate(order(15, token=token)).verdict is Verdict.ALLOW
    d = engine.evaluate(order(15, token=token))
    assert d.verdict is Verdict.DENY
    assert "already used" in d.reason


def test_expired_token(engine):
    req = order(15)
    token = engine.mint_approval_token(req.operation, req.params, ttl_seconds=-1)
    d = engine.evaluate(order(15, token=token))
    assert d.verdict is Verdict.NEEDS_APPROVAL
    assert "expired" in d.reason


def test_garbage_token_denied(engine):
    d = engine.evaluate(order(15, token="not-a-real-token"))
    assert d.verdict is Verdict.DENY
    assert "invalid signature" in d.reason


# --------------------------------------------------------------------- #
#  Audit log
# --------------------------------------------------------------------- #

def test_every_evaluation_audited(engine, tmp_path):
    engine.evaluate(Request("get_accounts"))
    engine.evaluate(Request("send"))
    engine.evaluate(order(50))
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
