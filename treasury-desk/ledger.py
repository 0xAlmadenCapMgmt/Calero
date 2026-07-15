"""
The executable substrate: a mock stablecoin ledger and a mock market venue.

This is the "downstream API" the governed agents actually mutate. Where
alice-bob only *judges* intents, here an ALLOW verdict moves real balances, so
we can assert after the fact that no unauthorized side effect ever occurred.

Money is integer cents throughout — no floats, so conservation checks are exact.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class InsufficientFunds(Exception):
    """Raised when a transfer would overdraw an account."""


@dataclass
class Transfer:
    src: str
    dst: str
    amount: int   # cents


class MockLedger:
    """Integer-cent balances plus an append-only log of every movement.

    `transactions` is the record the invariants read: the ground truth of what
    actually happened, independent of what any policy engine claims it allowed.
    """

    def __init__(self, opening: dict[str, int] | None = None):
        self.balances: dict[str, int] = dict(opening or {})
        self.transactions: list[Transfer] = []

    def balance(self, account: str) -> int:
        return self.balances.get(account, 0)

    def transfer(self, src: str, dst: str, amount: int) -> Transfer:
        if amount <= 0:
            raise ValueError(f"transfer amount must be positive, got {amount}")
        if self.balance(src) < amount:
            raise InsufficientFunds(
                f"{src} has {self.balance(src)}c, cannot send {amount}c"
            )
        self.balances[src] = self.balance(src) - amount
        self.balances[dst] = self.balance(dst) + amount
        t = Transfer(src, dst, amount)
        self.transactions.append(t)
        return t

    def total(self) -> int:
        """Sum of all balances — should be constant across the whole run."""
        return sum(self.balances.values())


@dataclass
class MockMarket:
    """A price-taking venue David buys assets from. Not an agent.

    A buy debits David's cash to the `market` account on the ledger (an allowed,
    non-agent edge) and credits David's holdings in whole units at a fixed price.
    """

    ledger: MockLedger
    prices_usd: dict[str, float] = field(
        default_factory=lambda: {"BTC-USD": 60000.0, "ETH-USD": 3000.0}
    )
    holdings: dict[str, float] = field(default_factory=dict)

    def buy(self, buyer: str, product: str, usd_cents: int) -> float:
        if product not in self.prices_usd:
            raise ValueError(f"no market price for {product}")
        self.ledger.transfer(buyer, "market", usd_cents)
        units = (usd_cents / 100.0) / self.prices_usd[product]
        self.holdings[product] = self.holdings.get(product, 0.0) + units
        return units
