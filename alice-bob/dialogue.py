"""Alice & Bob: two Claude-backed personas talking about money.

Two agents converse about checking, savings, and stock investments the way
two humans would. Neither agent has any tools, API access, or connection to
a bank or brokerage — each one's entire world is the other's last message,
the same structural-enforcement idea as the governance layer in the parent
project.

Alongside each spoken reply, an agent emits zero or more structured
*intents*: money movements it says it wants to make. Intents are printed
but never executed. They are shaped to match the parent core's
Request(operation, params) so a future step can submit them to
PolicyEngine.evaluate() — see intent_to_request().

Usage:
    python dialogue.py                # 6 exchanges with claude-opus-4-8
    python dialogue.py --turns 2      # shorter run
"""

from __future__ import annotations

import argparse
import os
import sys
from enum import Enum
from typing import List, Optional

import anthropic
from pydantic import BaseModel, Field, ValidationError


# ---------------------------------------------------------------------------
# Structured output: what each persona produces every turn
# ---------------------------------------------------------------------------


class IntentKind(str, Enum):
    TRANSFER = "transfer"
    BUY = "buy"
    SELL = "sell"
    CHECK_BALANCE = "check_balance"


class Intent(BaseModel):
    """A money movement the persona *wants* — never executed here."""

    kind: IntentKind
    from_account: Optional[str] = Field(
        default=None, description="Source account for a transfer, e.g. 'checking'."
    )
    to_account: Optional[str] = Field(
        default=None, description="Destination account for a transfer, e.g. 'savings'."
    )
    instrument: Optional[str] = Field(
        default=None, description="Ticker or product for buy/sell, e.g. 'VTI'."
    )
    amount_usd: Optional[str] = Field(
        default=None, description="Dollar amount as a plain number string, e.g. '200'."
    )
    rationale: str = Field(description="One sentence: why the persona wants this.")


class Turn(BaseModel):
    message: str = Field(description="What the persona says out loud, 2-4 sentences.")
    intents: List[Intent] = Field(
        description="Money movements the persona expressed wanting in this message. "
        "Empty if the message proposes no concrete movement."
    )


# ---------------------------------------------------------------------------
# Governance foreshadowing: Intent -> core Request shape
# ---------------------------------------------------------------------------

# intent_to_request stays a pure function producing a plain dict shaped like
# the core's Request ({"operation": ..., "params": {...}}), so the mapping is
# testable offline. load_policy_engine() imports the parent core and Coinbase
# adapter at runtime and every intent is judged against the Coinbase policy.yaml.

# Personas talk in bare tickers ("BTC"); the parent policy allowlists
# exchange products ("BTC-USD").
_PRODUCT_ALIASES = {"BTC": "BTC-USD", "ETH": "ETH-USD"}


def intent_to_request(intent: Intent) -> dict:
    """Map a conversational intent to the governance layer's request shape."""
    if intent.kind is IntentKind.CHECK_BALANCE:
        return {"operation": "get_accounts", "params": {}}
    if intent.kind is IntentKind.TRANSFER:
        return {
            "operation": "create_transfer",   # on the policy's forbidden list
            "params": {
                "from_account": intent.from_account or "",
                "to_account": intent.to_account or "",
                "amount_usd": intent.amount_usd or "",
            },
        }
    # buy / sell -> the create_order shape the parent PolicyEngine rules
    # key on (product_id, side, quote_size)
    instrument = (intent.instrument or "").upper()
    return {
        "operation": "create_order",
        "params": {
            "product_id": _PRODUCT_ALIASES.get(instrument, intent.instrument or ""),
            "side": intent.kind.value.upper(),
            "quote_size": intent.amount_usd or "",
        },
    }


def load_policy_engine():
    """Import the parent Calero core and load the Coinbase adapter + policy.

    Returns (engine, Request class), or (None, None) when the subproject is
    run outside the Calero repo — intents are then printed unjudged.
    Active hours are dropped (in memory only), as in the parent's demo.py,
    so the conversation can run at any time of day.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    policy = os.path.join(root, "adapters", "coinbase", "policy.yaml")
    if not os.path.exists(policy):
        return None, None
    sys.path.insert(0, root)
    try:
        from core import PolicyEngine, Request
        from adapters.coinbase import CoinbaseAdapter
    except ImportError:
        return None, None
    engine = PolicyEngine(
        policy, adapter=CoinbaseAdapter(), overrides={"active_hours_utc": None}
    )
    return engine, Request


_VERDICT_ICONS = {"ALLOW": "✅", "DENY": "⛔", "NEEDS_APPROVAL": "✋"}


def describe_intent(intent: Intent) -> str:
    req = intent_to_request(intent)
    if intent.kind is IntentKind.TRANSFER:
        what = f"move ${intent.amount_usd} {intent.from_account} -> {intent.to_account}"
    elif intent.kind is IntentKind.CHECK_BALANCE:
        what = "check balances"
    else:
        what = f"{intent.kind.value} ${intent.amount_usd} {intent.instrument}"
    return f"  ↳ intent: {what} → {req['operation']} {req['params']}"


# ---------------------------------------------------------------------------
# The personas
# ---------------------------------------------------------------------------

SHARED_RULES = """
You are role-playing a real person in a casual chat with a friend about
personal finances: checking accounts, savings, and stock investments.

Hard constraints:
- You have NO access to any bank account, brokerage, or financial system.
  You can only talk. Never claim to have executed anything.
- Keep each reply short and conversational: 2-4 sentences, first person,
  responding directly to what your friend just said.
- Whenever your message expresses wanting to actually move money or trade
  (e.g. "I should shift $500 to savings", "I'm thinking of buying $200 of
  VTI"), record it in `intents` with the amount and accounts/ticker you
  mentioned. If the message is just discussion with no concrete move,
  leave `intents` empty.
"""

ALICE_SYSTEM = SHARED_RULES + """
You are Alice: a cautious, methodical planner. You care about emergency-fund
sizing, keeping only a small float in checking, high-yield savings rates,
and dollar-cost averaging into broad index funds. You gently push back on
risky ideas but stay warm and curious about your friend Bob's thinking.
"""

BOB_SYSTEM = SHARED_RULES + """
You are Bob: more risk-tolerant and a little impulsive. You like individual
stocks, follow market news, and often float concrete trades to see what your
friend Alice thinks. You also dabble in crypto with small amounts — you'll
sometimes mention putting $5-20 into bitcoin or ethereum. You respect
Alice's caution even when you don't share it.
"""


class PersonaAgent:
    """One conversational persona. Its only input is the other persona's words."""

    def __init__(self, client: anthropic.Anthropic, name: str, system: str, model: str):
        self.client = client
        self.name = name
        self.system = system
        self.model = model
        self.messages: list[dict] = []

    def respond(self, incoming: str, attempts: int = 3) -> Turn:
        self.messages.append({"role": "user", "content": incoming})
        last_error: Optional[Exception] = None
        for _ in range(attempts):
            try:
                response = self.client.messages.parse(
                    model=self.model,
                    max_tokens=2000,
                    system=self.system,
                    messages=self.messages,
                    output_format=Turn,
                )
            except ValidationError as e:
                # The model occasionally emits malformed/truncated JSON;
                # history is unchanged, so just ask again.
                last_error = e
                continue
            if response.stop_reason == "refusal":
                raise RuntimeError(f"{self.name}'s model refused to respond.")
            if response.stop_reason == "max_tokens":
                last_error = RuntimeError("reply truncated at max_tokens")
                continue
            turn = response.parsed_output
            # Keep history as plain text so each agent only ever sees speech,
            # not the other's (or its own) structured internals.
            self.messages.append({"role": "assistant", "content": turn.message})
            return turn
        raise RuntimeError(
            f"{self.name} failed to produce a valid reply after {attempts} attempts"
        ) from last_error


# ---------------------------------------------------------------------------
# The conversation loop
# ---------------------------------------------------------------------------

DEFAULT_OPENER = (
    "You've just sat down for coffee with Bob. Kick off a conversation about "
    "how you're each organizing your checking account, savings, and stock "
    "investments this year."
)


def run_conversation(turns: int, model: str, opener: str) -> None:
    client = anthropic.Anthropic()
    alice = PersonaAgent(client, "Alice", ALICE_SYSTEM, model)
    bob = PersonaAgent(client, "Bob", BOB_SYSTEM, model)

    engine, request_cls = load_policy_engine()
    if engine is None:
        print("(parent PolicyEngine not found — intents will be printed unjudged)")

    incoming = opener
    speaker, listener = alice, bob
    for _ in range(turns * 2):
        turn = speaker.respond(incoming)
        print(f"\n{speaker.name}: {turn.message}")
        for intent in turn.intents:
            print(describe_intent(intent))
            if engine is not None:
                req = intent_to_request(intent)
                decision = engine.evaluate(
                    request_cls(operation=req["operation"], params=req["params"])
                )
                icon = _VERDICT_ICONS.get(decision.verdict.value, "?")
                print(
                    f"    {icon} PolicyEngine: {decision.verdict.value} — "
                    f"{decision.reason} (judged only; nothing executes)"
                )
        incoming = turn.message
        speaker, listener = listener, speaker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--turns", type=int, default=6,
        help="Number of exchanges (each exchange = one Alice + one Bob message).",
    )
    parser.add_argument("--model", default="claude-opus-4-8")
    parser.add_argument(
        "--opener", default=DEFAULT_OPENER,
        help="Seed prompt handed to Alice to start the conversation.",
    )
    args = parser.parse_args()
    try:
        run_conversation(args.turns, args.model, args.opener)
    except anthropic.APIConnectionError:
        sys.exit("Network error reaching the Claude API.")
    except anthropic.AuthenticationError:
        sys.exit(
            "No valid Claude API credentials. Set ANTHROPIC_API_KEY or run "
            "`ant auth login`."
        )
    except TypeError as e:
        # The SDK raises TypeError when no credential source exists at all.
        if "authentication" in str(e).lower():
            sys.exit(
                "No Claude API credentials found. Set ANTHROPIC_API_KEY or "
                "run `ant auth login`."
            )
        raise


if __name__ == "__main__":
    main()
