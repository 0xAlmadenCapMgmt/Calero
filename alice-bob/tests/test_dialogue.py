"""Offline tests: intent mapping + live judgment by the parent PolicyEngine.

No Claude API calls anywhere — only the pure mapping functions and the
parent project's policy engine evaluating mapped intents against policy.yaml.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from dialogue import (
    Intent,
    IntentKind,
    describe_intent,
    intent_to_request,
    load_policy_engine,
)


# ---------------------------------------------------------------------------
# Pure mapping: Intent -> Request shape
# ---------------------------------------------------------------------------


def test_buy_maps_to_create_order():
    intent = Intent(
        kind=IntentKind.BUY,
        instrument="VTI",
        amount_usd="200",
        rationale="Dollar-cost averaging into the market.",
    )
    req = intent_to_request(intent)
    assert req["operation"] == "create_order"
    assert req["params"]["product_id"] == "VTI"
    assert req["params"]["side"] == "BUY"
    # quote_size must be parsable — the parent PolicyEngine fails closed
    # on unparseable order sizes.
    assert float(req["params"]["quote_size"]) == 200.0


def test_crypto_ticker_normalized_to_exchange_product():
    intent = Intent(
        kind=IntentKind.BUY, instrument="btc", amount_usd="10", rationale="Dabbling."
    )
    req = intent_to_request(intent)
    assert req["params"]["product_id"] == "BTC-USD"


def test_sell_maps_to_create_order_sell_side():
    intent = Intent(
        kind=IntentKind.SELL,
        instrument="TSLA",
        amount_usd="150",
        rationale="Taking some profit.",
    )
    req = intent_to_request(intent)
    assert req["operation"] == "create_order"
    assert req["params"]["side"] == "SELL"


def test_transfer_maps_to_forbidden_create_transfer():
    intent = Intent(
        kind=IntentKind.TRANSFER,
        from_account="checking",
        to_account="savings",
        amount_usd="500",
        rationale="Topping up the emergency fund.",
    )
    req = intent_to_request(intent)
    assert req["operation"] == "create_transfer"
    assert req["params"] == {
        "from_account": "checking",
        "to_account": "savings",
        "amount_usd": "500",
    }


def test_check_balance_maps_to_get_accounts():
    intent = Intent(kind=IntentKind.CHECK_BALANCE, rationale="Curious where I stand.")
    req = intent_to_request(intent)
    assert req == {"operation": "get_accounts", "params": {}}


def test_describe_intent_names_the_operation():
    intent = Intent(
        kind=IntentKind.BUY, instrument="VTI", amount_usd="200", rationale="DCA."
    )
    line = describe_intent(intent)
    assert "create_order" in line
    assert "VTI" in line


# ---------------------------------------------------------------------------
# The wired-up governance layer: mapped intents judged by the parent engine
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine_and_request():
    engine, request_cls = load_policy_engine()
    assert engine is not None, "parent core / adapters/coinbase/policy.yaml not found"
    return engine, request_cls


def evaluate(engine_and_request, intent):
    engine, request_cls = engine_and_request
    req = intent_to_request(intent)
    return engine.evaluate(
        request_cls(operation=req["operation"], params=req["params"])
    )


def test_stock_buy_denied_by_product_allowlist(engine_and_request):
    decision = evaluate(
        engine_and_request,
        Intent(kind=IntentKind.BUY, instrument="NVDA", amount_usd="500",
               rationale="AI story has legs."),
    )
    assert decision.verdict.value == "DENY"
    assert "allowed-products" in decision.failed_rules


def test_transfer_denied_as_forbidden_operation(engine_and_request):
    decision = evaluate(
        engine_and_request,
        Intent(kind=IntentKind.TRANSFER, from_account="checking",
               to_account="savings", amount_usd="500", rationale="Emergency fund."),
    )
    assert decision.verdict.value == "DENY"
    assert "forbidden" in decision.reason


def test_small_btc_buy_allowed(engine_and_request):
    decision = evaluate(
        engine_and_request,
        Intent(kind=IntentKind.BUY, instrument="BTC", amount_usd="5",
               rationale="Dabbling."),
    )
    assert decision.verdict.value == "ALLOW"


def test_midsize_btc_buy_needs_human_approval(engine_and_request):
    decision = evaluate(
        engine_and_request,
        Intent(kind=IntentKind.BUY, instrument="BTC", amount_usd="15",
               rationale="Feeling lucky."),
    )
    assert decision.verdict.value == "NEEDS_APPROVAL"


def test_balance_check_allowed(engine_and_request):
    decision = evaluate(
        engine_and_request,
        Intent(kind=IntentKind.CHECK_BALANCE, rationale="Where do I stand?"),
    )
    assert decision.verdict.value == "ALLOW"
