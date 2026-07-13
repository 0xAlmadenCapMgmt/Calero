# Coinbase adapter — the reference platform for Calero

This is the reference adapter for the [Calero governance core](../../README.md): a policy-as-code governance layer for a Coinbase trading agent. It was the project's original use case, and it now runs on the unchanged platform-agnostic core, teaching that core how to read a Coinbase request.

## What the adapter supplies

The core knows how to run declarative rules against a `params.*` + `derived.*` context; it does not know what a Coinbase order *means*. `CoinbaseAdapter` (in [adapter.py](adapter.py)) fills that gap for `create_order`:

- `derived.side` — the order side, upper-cased.
- `derived.notional_usd` — the order size parsed from `quote_size` (dollars; fails closed to `None` on garbage, so a cap can never be bypassed by an unparseable amount).
- `derived.daily_notional_after` / `derived.daily_order_count` — the running per-UTC-day totals, read before the order and advanced only after an ALLOW (denied orders never consume budget).

Read-only operations (`get_accounts`, `get_product`, …) carry no derived facts and are governed by the policy's operation allowlist alone. The money-parsing and daily-total helpers come from [`adapters/common.py`](../common.py), shared with the payments adapter.

## policy.yaml

The heart of the system and the only file that changes when the rules change. It declares the platform controls (which operations exist at all, which are permanently forbidden — withdrawals and on-chain sends — the rate limit, the UTC active-hours window, and a kill-switch file path: `touch /tmp/coinbase-agent-KILL` freezes the agent instantly). Below those sit the business rules: a product allowlist (BTC-USD / ETH-USD), a buy-only constraint, a per-order dollar cap, a rolling daily spend cap, a daily order-count cap, and a threshold above which a human must approve each order.

## demo.py

A runnable demonstration using a mock Coinbase client, so it executes safely with no API key and no network access. It walks through nine scenarios: a permitted balance read, a small buy within limits, a buy over the per-order cap (denied), a mid-size buy that triggers the approval gate, the same buy succeeding with a valid minted token, the same token replayed (denied — single-use), a sell attempt (denied by the buy-only rule), a withdrawal attempt (denied by the forbidden list), and an unknown operation (denied by default). It finishes by printing the tail of the audit log. The demo removes the active-hours rule via an in-memory policy override so the other rules can be exercised at any time of day; `policy.yaml` itself is untouched.

```sh
# from the repo root
../../.venv/bin/python -m adapters.coinbase.demo
```

## rego/

The same business rules re-implemented in Rego for Open Policy Agent, with native `opa test` unit tests, sample inputs for `opa eval`, and a README comparing the two forms and describing when to graduate from a homegrown engine to OPA. Nothing in the main project requires the `opa` binary.

## How this layers with Coinbase's own controls

This layer is defense-in-depth, not a replacement for API key scopes. The key's permissions, enforced by Coinbase's servers, are the outer wall: a read-only key cannot trade no matter what any local code says. This governance layer adds the fine-grained rules Coinbase cannot express — dollar caps, buy-only constraints, approval workflows, trading hours — plus a complete audit trail. Use the narrowest key scope the agent's job requires and this policy layer on top of it, and never grant transfer or withdrawal permission on the key at all.

## Connecting to the real API

Install `coinbase-advanced-py`, construct the official `RESTClient` with your API credentials, and hand it to `GovernedClient` in place of the mock:

```python
from coinbase.rest import RESTClient
from core import GovernedClient, PolicyEngine
from adapters.coinbase import CoinbaseAdapter

raw = RESTClient(api_key=..., api_secret=...)
engine = PolicyEngine("adapters/coinbase/policy.yaml", adapter=CoinbaseAdapter())
client = GovernedClient(raw, engine)   # the only object the agent gets
```
