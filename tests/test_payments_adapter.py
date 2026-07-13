"""Tests for the Payments adapter: the same core, a different platform. These
mirror the Coinbase suite's shape against a payouts API whose amounts arrive in
cents and whose recipient allowlist is a pure params-only rule."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import PolicyEngine, Request, Verdict  # noqa: E402

from adapters.payments import PaymentsAdapter  # noqa: E402

SECRET = "test-secret"

# The real policy file is time-independent already (no active_hours), so load it
# directly — this also asserts the shipped policy.yaml stays consistent.
POLICY = Path(__file__).resolve().parents[1] / "adapters" / "payments" / "policy.yaml"


@pytest.fixture
def engine(tmp_path):
    return PolicyEngine(
        POLICY,
        adapter=PaymentsAdapter(),
        approval_secret=SECRET,
        overrides={
            "kill_switch_file": str(tmp_path / "KILL"),
            "audit": {"log_file": str(tmp_path / "audit.jsonl")},
        },
    )


def payout(dollars, destination="acct_payroll", token=None):
    return Request(
        "create_payout",
        {"destination": destination, "amount": str(int(dollars * 100))},  # cents
        approval_token=token,
    )


def refund(dollars):
    return Request("create_refund", {"charge": "ch_1", "amount": str(int(dollars * 100))})


def test_read_op_allowed(engine):
    assert engine.evaluate(Request("get_balance")).verdict is Verdict.ALLOW


def test_small_payout_allowed(engine):
    assert engine.evaluate(payout(150)).verdict is Verdict.ALLOW


def test_non_allowlisted_destination_denied(engine):
    d = engine.evaluate(payout(50, destination="acct_stranger"))
    assert d.verdict is Verdict.DENY
    assert "allowed-destinations" in d.failed_rules


def test_forbidden_transfer_denied(engine):
    d = engine.evaluate(Request("create_transfer", {"to": "x", "amount": "100"}))
    assert d.verdict is Verdict.DENY
    assert "forbidden" in d.reason


def test_per_payout_cap(engine):
    d = engine.evaluate(payout(900))
    assert d.verdict is Verdict.DENY
    assert "per-payout-cap" in d.failed_rules


def test_cents_scaling(engine):
    """$1.50 == 150 cents must read as 1.5 dollars, well under every cap."""
    ctx = engine._build_context(payout(1.5))
    assert ctx["derived"]["amount_usd"] == 1.5


def test_unparseable_amount_fails_closed(engine):
    d = engine.evaluate(
        Request("create_payout", {"destination": "acct_payroll", "amount": "lots"})
    )
    assert d.verdict is Verdict.DENY
    assert "positive-amount" in d.failed_rules


def test_daily_payout_cap(engine):
    # Cap is $2000/day; four $500 payouts reach it exactly, the fifth breaches
    # it. $500 is over the $200 approval threshold, so each needs a token.
    def approved_500():
        req = payout(500)
        req.approval_token = engine.mint_approval_token(req.operation, req.params)
        return engine.evaluate(req)

    for _ in range(4):
        assert approved_500().verdict is Verdict.ALLOW
    d = approved_500()   # would push the day's total to $2500 > $2000
    assert d.verdict is Verdict.DENY
    assert "daily-payout-cap" in d.failed_rules


def test_over_threshold_needs_approval(engine):
    d = engine.evaluate(payout(350))
    assert d.verdict is Verdict.NEEDS_APPROVAL
    assert "payout-approval-threshold" in d.failed_rules


def test_approved_payout_allows(engine):
    req = payout(350)
    token = engine.mint_approval_token(req.operation, req.params)
    req.approval_token = token
    assert engine.evaluate(req).verdict is Verdict.ALLOW


def test_refund_within_cap_allowed(engine):
    assert engine.evaluate(refund(40)).verdict is Verdict.ALLOW


def test_refund_over_cap_denied(engine):
    d = engine.evaluate(refund(250))
    assert d.verdict is Verdict.DENY
    assert "per-refund-cap" in d.failed_rules
