"""
Scenario-local adapters for the two agents. Both are thin compositions over the
reusable primitives in adapters/common.py — the governance core and its
Adapter contract are used unchanged.

Amounts are dollar strings in requests (like the Coinbase adapter's quote_size);
the ledger works in cents and converts at its edge. So these adapters parse
dollars with the default scale.

Counterparty rules ("only send to David / return to Catherine") are pure
params.* checks in the policy files and need no derivation here — the adapters
supply only the money facts and running daily totals.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Repo root on the path so `core` and the top-level `adapters` package resolve.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import Request

from adapters.common import DailyAccumulator, parse_money


class TreasuryAdapter:
    """Catherine. Governs send_stablecoin: amount + running daily disbursement."""

    def __init__(self) -> None:
        self._daily = DailyAccumulator()   # date -> USD sent / send count

    def derive(self, request: Request) -> dict:
        derived: dict[str, Any] = {}
        if request.operation == "send_stablecoin":
            derived["amount_usd"] = parse_money(request.params.get("amount"))
            if derived["amount_usd"] is not None:
                derived["daily_sent_after"] = self._daily.total() + derived["amount_usd"]
            derived["daily_send_count"] = self._daily.count()
        return derived

    def commit(self, request: Request, context: dict) -> None:
        if request.operation == "send_stablecoin":
            self._daily.add(context["derived"].get("amount_usd") or 0.0)


class FundManagerAdapter:
    """David. Governs two ops: create_order (invest) and return_funds (to
    Catherine). Each has its own daily accumulator so caps don't cross-talk."""

    def __init__(self) -> None:
        self._daily_trade = DailyAccumulator()    # date -> USD traded / order count
        self._daily_return = DailyAccumulator()   # date -> USD returned / return count

    def derive(self, request: Request) -> dict:
        derived: dict[str, Any] = {}
        if request.operation == "create_order":
            derived["side"] = str(request.params.get("side", "")).upper()
            derived["notional_usd"] = parse_money(request.params.get("quote_size"))
            if derived["notional_usd"] is not None:
                derived["daily_notional_after"] = (
                    self._daily_trade.total() + derived["notional_usd"]
                )
            derived["daily_order_count"] = self._daily_trade.count()
        elif request.operation == "return_funds":
            derived["amount_usd"] = parse_money(request.params.get("amount"))
            if derived["amount_usd"] is not None:
                derived["daily_returned_after"] = (
                    self._daily_return.total() + derived["amount_usd"]
                )
        return derived

    def commit(self, request: Request, context: dict) -> None:
        if request.operation == "create_order":
            self._daily_trade.add(context["derived"].get("notional_usd") or 0.0)
        elif request.operation == "return_funds":
            self._daily_return.add(context["derived"].get("amount_usd") or 0.0)
