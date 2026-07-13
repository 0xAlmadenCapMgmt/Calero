# Payments adapter — a second platform on the same core

A Stripe/bank-style payouts agent, built on the [Calero governance core](../../README.md) with **no changes to the core**. Its job in this repo is to prove the layer is genuinely platform-agnostic: a different API surface and a different parameter shape, absorbed entirely by the adapter, judged by the same engine that judges the Coinbase adapter.

## What differs from Coinbase (and why that matters)

- **Different operations.** `create_payout` / `create_refund` / `get_balance`, not `create_order`. Raw `create_transfer` and opening a new destination account are permanently forbidden.
- **Different param shape.** Amounts arrive in integer **cents**, while the policy is written in dollars. `PaymentsAdapter.derive()` scales by 100 (`amount_cents / 100`) — a concrete demonstration that unit handling lives in the adapter, not the core. Coinbase's `quote_size` is already dollars and needs no scaling.
- **A params-only rule.** The recipient allowlist is enforced directly on `params.destination` (`op: in`) with no derived fact at all — a reminder that not every rule needs the adapter; simple checks ride on the raw request.

The adapter supplies just the money facts and the running daily payout total, reusing [`adapters/common.py`](../common.py)'s `parse_money` and `DailyAccumulator` — the same helpers the Coinbase adapter uses.

## policy.yaml

Platform controls (kill switch at `/tmp/payments-agent-KILL`, operation allow/forbid lists, rate limit) plus the business rules: a recipient allowlist, a per-payout cap ($500), a rolling daily payout cap ($2000), a daily payout-count cap, a per-refund cap ($100), and an approval threshold ($200) above which a human must mint a token. This policy intentionally omits `active_hours_utc`, so the demo runs at any time with no override — showing active hours is optional.

## demo.py

A runnable demonstration with a mock payments client (no API key, no network). Nine scenarios: a balance read, an in-limit payout to an approved vendor, a payout to a non-allowlisted recipient (denied), a payout over the per-payout cap (denied), an over-threshold payout without a token (needs approval), the same succeeding with a minted token, a raw transfer (denied — forbidden), a refund within the per-refund cap, and a refund over it (denied). It prints the audit tail.

```sh
# from the repo root
../../.venv/bin/python -m adapters.payments.demo
```

Notably, nothing in this directory imports anything Coinbase-specific — only `core` plus this platform's own adapter and policy. That is the whole point.
