"""Demonstrates the governance layer using a mock Coinbase client."""

import os

from governed_client import ApprovalRequired, GovernedClient, PolicyViolation
from policy_engine import PolicyEngine


class MockCoinbase:
    """Stands in for coinbase.rest.RESTClient in this demo."""

    def get_accounts(self):
        return {"accounts": [{"name": "BTC Wallet", "balance": "0.5 BTC"}]}

    def create_order(self, **kw):
        return {"order_id": "mock-123", **kw}

    def send(self, **kw):  # should never be reachable through governance
        return {"sent": True}


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

    # Drop the active-hours rule for the demo so every other rule can fire
    # regardless of when it is run. `overrides` tweaks the loaded policy in
    # memory; policy.yaml itself is untouched.
    engine = PolicyEngine("policy.yaml", overrides={"active_hours_utc": None})
    client = GovernedClient(MockCoinbase(), engine)

    print("1. Read balance (allowed op):")
    attempt("get_accounts", lambda: client.call("get_accounts"))

    print("\n2. Small BUY within limits:")
    small = {"product_id": "BTC-USD", "side": "BUY", "quote_size": "5.00"}
    attempt("create_order $5", lambda: client.call("create_order", small))

    print("\n3. BUY above per-order cap ($25):")
    big = {"product_id": "BTC-USD", "side": "BUY", "quote_size": "50.00"}
    attempt("create_order $50", lambda: client.call("create_order", big))

    print("\n4. BUY above approval threshold ($10) without a token:")
    mid = {"product_id": "BTC-USD", "side": "BUY", "quote_size": "15.00"}
    attempt("create_order $15", lambda: client.call("create_order", mid))

    print("\n5. Same order WITH a human-minted approval token:")
    token = engine.mint_approval_token("create_order", mid)
    attempt(
        "create_order $15 + token",
        lambda: client.call("create_order", mid, approval_token=token),
    )

    print("\n6. Replaying the SAME token (single-use, now consumed):")
    attempt(
        "create_order $15 + replayed token",
        lambda: client.call("create_order", mid, approval_token=token),
    )

    print("\n7. SELL (side not allowed by the buy-only rule):")
    sell = {"product_id": "BTC-USD", "side": "SELL", "quote_size": "5.00"}
    attempt("create_order SELL", lambda: client.call("create_order", sell))

    print("\n8. Withdrawal attempt (forbidden op):")
    attempt("send", lambda: client.call("send", {"to": "0xabc", "amount": "1 BTC"}))

    print("\n9. Unknown operation (deny by default):")
    attempt("delete_everything", lambda: client.call("delete_everything"))

    print("\nAudit trail written to audit.log.jsonl:")
    with open("audit.log.jsonl") as f:
        for line in f.readlines()[-3:]:
            print("  " + line.strip())
