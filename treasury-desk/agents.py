"""
The two governed agents. Each `build_*` returns a (GovernedClient, PolicyEngine)
pair: the client is the ONLY object its agent is ever handed, and it wraps a
ledger-facing client behind the policy engine. Neither agent is given the other's
client or the raw ledger, so "David can only reach Catherine" is enforced
structurally (isolation) as well as by policy (the counterparty allowlists).

Requests carry dollar amounts as strings (like the Coinbase adapter's
quote_size); the ledger-facing clients convert to integer cents at the edge.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import GovernedClient, PolicyEngine   # noqa: E402

from desk_adapters import FundManagerAdapter, TreasuryAdapter   # noqa: E402
from ledger import MockLedger, MockMarket   # noqa: E402

_HERE = Path(__file__).resolve().parent
POLICY_CATHERINE = _HERE / "policy_catherine.yaml"
POLICY_DAVID = _HERE / "policy_david.yaml"


def _to_cents(amount: str) -> int:
    return int(round(float(amount) * 100))


class TreasuryDesk:
    """Catherine's ledger-facing client: read balance, disburse to a counterparty."""

    def __init__(self, ledger: MockLedger):
        self._ledger = ledger

    def get_balance(self) -> dict:
        return {"account": "catherine", "usd_cents": self._ledger.balance("catherine")}

    def send_stablecoin(self, to: str, amount: str) -> dict:
        self._ledger.transfer("catherine", to, _to_cents(amount))
        return {"sent_to": to, "amount": amount}


class FundDesk:
    """David's ledger-facing client: read balance, invest via the market venue,
    return capital to a counterparty."""

    def __init__(self, ledger: MockLedger, market: MockMarket):
        self._ledger = ledger
        self._market = market

    def get_balance(self) -> dict:
        return {"account": "david", "usd_cents": self._ledger.balance("david")}

    def create_order(self, product_id: str, side: str, quote_size: str) -> dict:
        units = self._market.buy("david", product_id, _to_cents(quote_size))
        return {"product_id": product_id, "side": side, "units": units}

    def return_funds(self, to: str, amount: str) -> dict:
        self._ledger.transfer("david", to, _to_cents(amount))
        return {"returned_to": to, "amount": amount}


def build_catherine(
    ledger: MockLedger, approval_secret: str, overrides: dict | None = None
) -> tuple[GovernedClient, PolicyEngine]:
    engine = PolicyEngine(
        POLICY_CATHERINE,
        adapter=TreasuryAdapter(),
        approval_secret=approval_secret,
        overrides=overrides,
    )
    return GovernedClient(TreasuryDesk(ledger), engine), engine


def build_david(
    ledger: MockLedger,
    market: MockMarket,
    approval_secret: str,
    overrides: dict | None = None,
) -> tuple[GovernedClient, PolicyEngine]:
    engine = PolicyEngine(
        POLICY_DAVID,
        adapter=FundManagerAdapter(),
        approval_secret=approval_secret,
        overrides=overrides,
    )
    return GovernedClient(FundDesk(ledger, market), engine), engine
