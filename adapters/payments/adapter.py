"""
Payments adapter — a second platform for the Calero governance core, to show
the core is genuinely platform-agnostic and not just Coinbase with indirection.

It models a Stripe/bank-style payouts API. Two things differ deliberately from
the Coinbase adapter, and both are absorbed by the same core:

  * a different API surface — create_payout / create_refund, not create_order;
  * a different param shape — amounts arrive in integer CENTS, while the policy
    is written in dollars, so derive() scales by 100. (Coinbase's quote_size is
    already dollars.)

The recipient allowlist is enforced as a pure params-only rule in policy.yaml
(params.destination in [...]) — no derivation needed — illustrating that not
every rule requires the adapter. The adapter only supplies the money facts and
the running daily payout total.
"""

from __future__ import annotations

from typing import Any

from core import Request

from ..common import DailyAccumulator, parse_money


class PaymentsAdapter:
    def __init__(self) -> None:
        self._daily = DailyAccumulator()   # date -> USD paid out / payout count

    def derive(self, request: Request) -> dict:
        derived: dict[str, Any] = {}
        if request.operation in ("create_payout", "create_refund"):
            # Amounts arrive in cents; rules are written in dollars.
            derived["amount_usd"] = parse_money(request.params.get("amount"), scale=100)
        if request.operation == "create_payout":
            if derived["amount_usd"] is not None:
                derived["daily_payout_after"] = (
                    self._daily.total() + derived["amount_usd"]
                )
            derived["daily_payout_count"] = self._daily.count()
        return derived

    def commit(self, request: Request, context: dict) -> None:
        if request.operation == "create_payout":
            amount = context["derived"].get("amount_usd") or 0.0
            self._daily.add(amount)
