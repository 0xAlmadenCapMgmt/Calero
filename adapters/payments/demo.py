"""Demonstrates the SAME governance core against a mock payments API.

Run from the repo root as a module:  python -m adapters.payments.demo
or directly:                          python adapters/payments/demo.py

Nothing here imports anything Coinbase-specific: only `core` plus this
platform's own adapter and policy. That is the whole point.
"""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import ApprovalRequired, GovernedClient, PolicyEngine, PolicyViolation

from adapters.payments import PaymentsAdapter

_HERE = Path(__file__).resolve().parent
POLICY = _HERE / "policy.yaml"
AUDIT_LOG = _HERE / "audit.log.jsonl"


class MockPayments:
    """Stands in for a Stripe/bank-style payouts client. Amounts are in cents."""

    def get_balance(self):
        return {"available_usd": 12000.00}

    def create_payout(self, **kw):
        return {"payout_id": "po_mock123", "status": "paid", **kw}

    def create_refund(self, **kw):
        return {"refund_id": "re_mock123", "status": "succeeded", **kw}

    def create_transfer(self, **kw):  # forbidden; never reachable via governance
        return {"transfer_id": "tr_mock123"}


def attempt(label, fn):
    try:
        result = fn()
        print(f"  ✅ {label}: {result}")
    except ApprovalRequired as e:
        print(f"  ✋ {label}: {e}")
    except PolicyViolation as e:
        print(f"  ⛔ {label}: {e}")


if __name__ == "__main__":
    os.environ.setdefault("AGENT_APPROVAL_SECRET", "demo-secret")

    engine = PolicyEngine(POLICY, adapter=PaymentsAdapter())
    client = GovernedClient(MockPayments(), engine)

    print("1. Read balance (allowed op):")
    attempt("get_balance", lambda: client.call("get_balance"))

    print("\n2. Small payout to an approved vendor ($150):")
    ok = {"destination": "acct_vendor_aws", "amount": "15000"}  # cents
    attempt("create_payout $150", lambda: client.call("create_payout", ok))

    print("\n3. Payout to a NON-approved recipient (destination allowlist):")
    bad_dest = {"destination": "acct_stranger", "amount": "5000"}
    attempt("create_payout stranger", lambda: client.call("create_payout", bad_dest))

    print("\n4. Payout above the per-payout cap ($500):")
    big = {"destination": "acct_payroll", "amount": "90000"}  # $900
    attempt("create_payout $900", lambda: client.call("create_payout", big))

    print("\n5. Payout above the approval threshold ($200) without a token:")
    mid = {"destination": "acct_payroll", "amount": "35000"}  # $350
    attempt("create_payout $350", lambda: client.call("create_payout", mid))

    print("\n6. Same payout WITH a human-minted approval token:")
    token = engine.mint_approval_token("create_payout", mid)
    attempt(
        "create_payout $350 + token",
        lambda: client.call("create_payout", mid, approval_token=token),
    )

    print("\n7. Raw transfer attempt (forbidden op):")
    attempt(
        "create_transfer",
        lambda: client.call("create_transfer", {"to": "acct_x", "amount": "100"}),
    )

    print("\n8. Refund within the per-refund cap ($40):")
    refund = {"charge": "ch_123", "amount": "4000"}  # $40
    attempt("create_refund $40", lambda: client.call("create_refund", refund))

    print("\n9. Refund above the per-refund cap ($100):")
    big_refund = {"charge": "ch_456", "amount": "25000"}  # $250
    attempt("create_refund $250", lambda: client.call("create_refund", big_refund))

    print(f"\nAudit trail written to {AUDIT_LOG.name}:")
    with open(AUDIT_LOG) as f:
        for line in f.readlines()[-3:]:
            print("  " + line.strip())
