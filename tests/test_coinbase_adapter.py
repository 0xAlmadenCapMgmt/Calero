"""Tests for the Coinbase adapter: the business rules it enables and the
`derived.*` facts it computes (order side, parsed notional, running daily
totals). Platform controls and the generic approval-token lifecycle are covered
once, platform-independently, in test_core.py; here we exercise what the
CoinbaseAdapter adds on top of the core.

Because policies are data judged by a generic engine, a policy edit that
weakens a guardrail fails these tests before it ever reaches the agent."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import PolicyEngine, Request, Verdict  # noqa: E402

from adapters.coinbase import CoinbaseAdapter  # noqa: E402

SECRET = "test-secret"


def base_policy(tmp_path):
    """A self-contained policy mirroring adapters/coinbase/policy.yaml, minus
    active hours (so tests are time-independent) and with tmp paths."""
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
    return PolicyEngine(
        base_policy(tmp_path), adapter=CoinbaseAdapter(), approval_secret=SECRET
    )


def order(quote_size, product="BTC-USD", side="BUY", token=None):
    return Request(
        "create_order",
        {"product_id": product, "side": side, "quote_size": str(quote_size)},
        approval_token=token,
    )


# --------------------------------------------------------------------- #
#  Business rules (need the adapter's derived facts)
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
    eng = PolicyEngine(policy, adapter=CoinbaseAdapter(), approval_secret=SECRET)
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
#  Approval gate keyed on the derived notional
# --------------------------------------------------------------------- #

def test_over_threshold_needs_approval(engine):
    d = engine.evaluate(order(15))
    assert d.verdict is Verdict.NEEDS_APPROVAL
    assert "human-approval-threshold" in d.failed_rules


def test_valid_token_allows(engine):
    req = order(15)
    token = engine.mint_approval_token(req.operation, req.params)
    assert engine.evaluate(order(15, token=token)).verdict is Verdict.ALLOW


# --------------------------------------------------------------------- #
#  Audit log
# --------------------------------------------------------------------- #

def test_orders_are_audited(engine, tmp_path):
    engine.evaluate(Request("get_accounts"))
    engine.evaluate(order(5))
    engine.evaluate(order(50))
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
