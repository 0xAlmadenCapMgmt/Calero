"""
Coinbase adapter — the reference adapter for the Calero governance core.

It teaches the generic engine how to read a Coinbase request: what the
`derived.*` facts referenced by adapters/coinbase/policy.yaml mean, and how the
running daily totals advance. This is the exact logic that once lived inside the
engine's _build_context; extracting it here is what makes the core
platform-agnostic.

Only `create_order` carries derived facts; read-only operations
(get_accounts, get_product, ...) need none and are governed by the policy's
operation allowlist alone.
"""

from __future__ import annotations

from typing import Any

from core import Request

from ..common import DailyAccumulator, parse_money


class CoinbaseAdapter:
    def __init__(self) -> None:
        self._daily = DailyAccumulator()   # date -> USD spent / order count

    def derive(self, request: Request) -> dict:
        derived: dict[str, Any] = {}
        if request.operation == "create_order":
            derived["side"] = str(request.params.get("side", "")).upper()
            # Coinbase order size arrives as a dollar string in `quote_size`
            # (older callers used `notional_usd`); already in the rule's unit.
            derived["notional_usd"] = parse_money(
                request.params.get("quote_size", request.params.get("notional_usd"))
            )
            if derived["notional_usd"] is not None:
                derived["daily_notional_after"] = (
                    self._daily.total() + derived["notional_usd"]
                )
            derived["daily_order_count"] = self._daily.count()
        return derived

    def commit(self, request: Request, context: dict) -> None:
        if request.operation == "create_order":
            notional = context["derived"].get("notional_usd") or 0.0
            self._daily.add(notional)
