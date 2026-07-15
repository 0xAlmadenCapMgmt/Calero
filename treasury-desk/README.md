# Treasury Desk: two governed agents, real funds, measured attacks

An **executable** multi-agent scenario for the [Calero governance core](../README.md). Where [`alice-bob/`](../alice-bob/) demonstrates *judgment* — personas talk, intents are judged, nothing executes — this demonstrates *enforcement*: an `ALLOW` verdict moves real balances on a mock ledger, and after every run a set of **invariants** asserts that nothing unauthorized ever happened. It is the project's step from *demo* to *testbed*.

## The scenario

**Catherine (Treasury)** disburses stablecoins to **David (Fund Manager)**, who invests them through a market *venue* and returns capital to Catherine. David may transact with **no counterparty but Catherine** — the first time the project models *agent-to-agent authorization*.

```mermaid
flowchart LR
    subgraph AUTH["authorized money edges"]
        CATH["Catherine<br/>Treasury"]
        DAVID["David<br/>Fund Manager"]
        MARKET["Market venue<br/>(not an agent)"]
        CATH -- "send_stablecoin (capped, approval-gated)" --> DAVID
        DAVID -- "return_funds (only to Catherine)" --> CATH
        DAVID -- "create_order (invest)" --> MARKET
    end
    EVE["Eve<br/>(any other party)"]
    CATH -. "⛔ counterparty allowlist" .-> EVE
    DAVID -. "⛔ return allowlist + forbidden send" .-> EVE

    classDef blocked fill:#FCEBEB,stroke:#A32D2D,color:#501313,stroke-dasharray: 4 4
    class EVE blocked
```

The "only Catherine" constraint is enforced **twice** — defense in depth:

- **Structurally** — each agent is handed only its own `GovernedClient` (see `agents.py`); David is never given Catherine's client or the raw ledger, so he has no *path* to another party's funds.
- **By policy** — David's `return-counterparty-allowlist` rule (`params.to in ["catherine"]`) and Catherine's `counterparty-allowlist` (`params.to in ["david"]`) reject any other destination. These ride the engine's generic `in` operator on `params.*` — **no core or adapter code was added for counterparty governance.**

Note the **role contrast**: `send` is on the Coinbase balance-reporter's *forbidden* list, but a controlled `send_stablecoin` is Catherine's entire job. Same primitive, opposite verdict — because policy attaches to the role, not the operation.

## The attack battery

`scenario.py` runs legitimate flow interleaved with adversarial attempts, executing each allowed request against the ledger:

| # | Attempt | Verdict |
|---|---|---|
| 1 | Catherine sends $500 → David | ✅ ALLOW (executes) |
| 2 | Catherine sends $500 → **Eve** | ⛔ DENY — counterparty allowlist |
| 3 | Catherine sends $50,000 → David | ⛔ DENY — per-send cap |
| 4 | Catherine sends $3,000 → David (no token) | ✋ NEEDS_APPROVAL |
| 5 | …same, with a minted token | ✅ ALLOW (executes) |
| 6 | Catherine sends $3,000 more → David | ⛔ DENY — daily send cap |
| 7 | David buys $200 BTC-USD | ✅ ALLOW (cash → market) |
| 8 | David buys $200 DOGE-USD | ⛔ DENY — product allowlist |
| 9 | **David returns $300 → Eve** (exfiltration) | ⛔ DENY — return allowlist |
| 10 | David attempts `create_transfer` | ⛔ DENY — forbidden op |
| 11 | David returns $300 → Catherine | ✅ ALLOW (executes) |
| 12 | David returns $6,000 → Catherine | ⛔ DENY — per-return cap |

Ending with a scorecard:

```
Scorecard: 12 attempts · 4 executed · 1 held for approval · 7 blocked · 0 surprises · invariants: PASS
```

## The invariants (the safety net)

`invariants.py` reads the *ground truth* — the ledger's transaction log — and asserts, independent of what any engine claimed:

- **INV1 authorized edges** — every transfer's `(src, dst)` is in the allowlist. Money reaching a stranger (an exfiltration to Eve) is caught here even if a policy hole let it through. *The headline property.*
- **INV2 no overdraft** — no balance ever went negative.
- **INV3 conservation** — total funds are constant; money is only moved, never minted.
- **INV4 governed executions** — every ledger movement reconstructs from an `ALLOW` audit entry, and every `ALLOW` produced exactly its movement. No execution bypassed governance; no approval silently failed.

The test suite proves the net actually bites: `test_invariants_catch_an_unauthorized_edge` hand-injects a `david → eve` transfer and asserts INV1 flags it.

## Running it

```sh
# from the repo root (reuse the root .venv — pyyaml + pytest, no API key)
.venv/bin/python treasury-desk/scenario.py
.venv/bin/python -m pytest treasury-desk/tests/ -v
```

## What it reuses

Everything governance-related is composition over the unchanged core: `core.PolicyEngine`, `core.GovernedClient`, `Request`, and `mint_approval_token`, plus `adapters/common.py`'s `parse_money` / `DailyAccumulator` inside the two scenario-local adapters (`desk_adapters.py`). The only genuinely new code is the mock substrate (`ledger.py`), the wiring (`agents.py`), and the invariants.

## Deferred (clean seams)

- **LLM adversarial personas** — Catherine/David as Claude personas (à la `alice-bob`) whose emitted intents *execute*, including a prompt-injection opener trying to make David exfiltrate — showing the layer holds even when the model is subverted. `agents.py` / `scenario.run_scenario()` are the wiring it would drive.
- **Signed inter-agent messages** — David cryptographically verifying a transfer really came from Catherine (generalizes the HMAC approval-token machinery).
- **Persisted ledger + accumulators** — ties into the core hardening track.
