"""Deterministic tests for the multi-agent testbed — no API key, CI-friendly.

Drives the two governed agents through the full attack battery and asserts, for
each attempt, the governance verdict AND the ledger side effect; then asserts
the post-run invariants hold. Also proves the invariants actually bite (a
manufactured unauthorized transfer is caught) and that isolation is structural.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agents import FundDesk, TreasuryDesk, build_david
from invariants import check_invariants
from ledger import InsufficientFunds, MockLedger, MockMarket, Transfer
from scenario import run_scenario


@pytest.fixture()
def run(tmp_path):
    return run_scenario(audit_dir=str(tmp_path))


def by_label(results):
    return {r["label"]: r for r in results}


# --------------------------------------------------------------------- #
#  The battery: every attempt lands on its expected verdict
# --------------------------------------------------------------------- #

def test_no_surprises_across_battery(run):
    surprises = [r for r in run["results"] if r["verdict"] != r["expected"]]
    assert surprises == [], surprises


def test_invariants_hold_after_full_run(run):
    assert run["violations"] == []


@pytest.mark.parametrize("label_frag,rule", [
    ("→ eve (stranger)", "counterparty-allowlist"),
    ("over per-send cap", "per-send-cap"),
    ("breaches daily cap", "daily-send-cap"),
    ("DOGE-USD", "allowed-products"),
    ("exfiltration", "return-counterparty-allowlist"),
    ("over return cap", "per-return-cap"),
])
def test_denied_attempts_cite_expected_rule(run, label_frag, rule):
    rec = next(r for r in run["results"] if label_frag in r["label"])
    assert rec["verdict"] == "DENY"
    assert rule in rec["failed_rules"]
    assert rec["executed"] is False


def test_forbidden_op_denied_without_a_business_rule(run):
    rec = by_label(run["results"])["create_transfer (forbidden op)"]
    assert rec["verdict"] == "DENY"
    assert "forbidden" in rec["detail"]
    assert rec["executed"] is False


def test_approval_gate_then_token(run):
    labels = by_label(run["results"])
    assert labels["send $3,000 → david (no token)"]["verdict"] == "NEEDS_APPROVAL"
    assert labels["send $3,000 → david (+ token)"]["verdict"] == "ALLOW"


def test_final_balances_conserved(run):
    ledger = run["ledger"]
    assert ledger.balances == {"catherine": 680000, "david": 300000, "market": 20000}
    assert ledger.total() == 1_000_000


def test_only_authorized_edges_touched_the_ledger(run):
    allowed = {("catherine", "david"), ("david", "catherine"), ("david", "market")}
    assert all((t.src, t.dst) in allowed for t in run["ledger"].transactions)


# --------------------------------------------------------------------- #
#  The safety net actually bites, and isolation is structural
# --------------------------------------------------------------------- #

def test_invariants_catch_an_unauthorized_edge(tmp_path):
    """If a transfer to a stranger somehow reached the ledger, INV1 flags it."""
    ledger = MockLedger({"david": 1000})
    ledger.transactions.append(Transfer("david", "eve", 1000))  # bypass, by hand
    market = MockMarket(ledger)
    violations = check_invariants(ledger, market, {}, opening_total=1000)
    assert any("INV1" in v and "eve" in v for v in violations)


def test_ledger_blocks_overdraft():
    """Even if policy allowed it, the substrate cannot overdraw an account."""
    ledger = MockLedger({"david": 100})
    with pytest.raises(InsufficientFunds):
        ledger.transfer("david", "catherine", 500)


def test_david_client_has_no_treasury_powers():
    """Structural isolation: David's client exposes no fund-sending method, and
    the treasury op isn't even in his allowed_operations."""
    assert not hasattr(FundDesk, "send_stablecoin")
    assert not hasattr(FundDesk, "send")
    ledger = MockLedger({"david": 100000})
    david, engine = build_david(ledger, MockMarket(ledger), "s")
    assert "send_stablecoin" not in engine.policy["allowed_operations"]
