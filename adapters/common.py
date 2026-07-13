"""
Shared building blocks for adapters.

Adapters translate a raw request into the `derived.*` facts a policy's rules
reference. Two needs recur across money-moving platforms: parsing an amount out
of loosely-typed params, and tracking running per-day totals. Both live here so
each adapter is a thin, declarative mapping rather than a re-implementation.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional


def today_utc() -> str:
    """The current UTC date as an ISO string — the key daily totals bucket on."""
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def parse_money(value, scale: float = 1.0) -> Optional[float]:
    """Parse a monetary amount, fail-closed.

    Returns a float, or None if `value` cannot be parsed — the caller surfaces
    None as a derived fact, and a rule comparing against None fails closed in
    the engine, so an unparseable amount can never slip past a cap.

    `scale` converts the source unit to the policy's unit: pass 100 when params
    carry cents but rules are written in dollars (amount_cents / 100 = dollars),
    or leave it 1.0 when params are already in the rule's unit.
    """
    try:
        return float(value) / scale
    except (TypeError, ValueError):
        return None


class DailyAccumulator:
    """Per-UTC-day running totals and counts.

    Owned by an adapter, mutated only via commit() after an ALLOW, so denied
    requests never consume budget. Reads (total/count) drive the `*_after`
    derived facts a policy checks before allowing the next request.
    """

    def __init__(self) -> None:
        self._total: dict[str, float] = {}
        self._count: dict[str, int] = {}

    def total(self, day: Optional[str] = None) -> float:
        return self._total.get(day or today_utc(), 0.0)

    def count(self, day: Optional[str] = None) -> int:
        return self._count.get(day or today_utc(), 0)

    def add(self, amount: float, day: Optional[str] = None) -> None:
        d = day or today_utc()
        self._total[d] = self._total.get(d, 0.0) + (amount or 0.0)
        self._count[d] = self._count.get(d, 0) + 1
