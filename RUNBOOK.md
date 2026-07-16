# Calero Runbook — set up, run, and log every component in sequence

This walks the four (plus one) pieces of Calero **in order**, from the
platform-agnostic core up to the live LLM adversarial layer, and shows how to
capture each run's results and transcripts for later recall.

```
core  →  adapters  →  judgment demo (alice-bob)  →  enforcement testbed (treasury-desk)  →  adversarial (LLM)
```

The deterministic stages need only `pyyaml` + `pytest`. The last two live stages
call the Claude API and are **gated on `ANTHROPIC_API_KEY`** — without it they
skip cleanly, so the whole sequence always completes.

## 1. Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # pyyaml, pytest (deterministic core)

# To enable the live stages (judgment dialogue + adversarial layer):
.venv/bin/pip install -r treasury-desk/requirements-llm.txt   # anthropic
export ANTHROPIC_API_KEY=sk-...                    # or: ant auth login
```

One venv with `anthropic` installed runs everything. Without a key, live model
calls skip; without `anthropic` at all, the judgment offline tests also skip.

## 2. Run everything, logged, in sequence

```sh
.venv/bin/python scripts/run_suite.py              # full sequence
.venv/bin/python scripts/run_suite.py --turns 6    # longer adversarial conversation
.venv/bin/python scripts/run_suite.py --only enforcement   # a single stage
```

Each run writes a timestamped `runs/<UTC-timestamp>/` directory:

| Artifact | Contents |
|---|---|
| `NN-<stage>.log` | full stdout/stderr transcript of that stage's commands |
| `summary.md` | table: stage · status · duration · log · what it proves |
| `manifest.json` | machine-readable results (status/duration/return codes per step) |
| `runs/latest` | symlink to the most recent run |

`runs/` is gitignored except the committed [`runs/sample-run/`](runs/sample-run/summary.md),
which shows the format. The harness exits non-zero if any non-skipped stage fails.

## 3. The stages, and how to run each by hand

| # | Stage | Proves | Manual command |
|---|---|---|---|
| 1 | **core** | Generic engine: platform controls, approval-token lifecycle, `NullAdapter`. | `.venv/bin/python -m pytest tests/test_core.py -v` |
| 2 | **adapters** | Both adapters + shared derivation; two demos run. | `pytest tests/test_coinbase_adapter.py tests/test_payments_adapter.py -v` · `python -m adapters.coinbase.demo` · `python -m adapters.payments.demo` |
| 3 | **judgment** (alice-bob) | Intents judged live by the engine; nothing executes. | `pytest alice-bob/tests/ -v` · `python alice-bob/dialogue.py --turns 2` *(live)* |
| 4 | **enforcement** (treasury-desk) | Governed agents move real (mock-ledger) funds; invariants hold. | `python treasury-desk/scenario.py` · `pytest treasury-desk/tests/test_treasury_desk.py -v` |
| 5 | **adversarial** (LLM) | A subverted persona cannot exfiltrate; invariants are the oracle. | `python treasury-desk/adversarial.py --turns 4 --attacker david` *(live)* · `pytest treasury-desk/tests/test_adversarial.py` |

## 4. The adversarial layer (stage 5) in detail

Catherine and David become Claude personas whose emitted intents **actually
execute** against the mock ledger through their governed clients. With
`--attacker david`, David is *subverted* — instructed to divert capital to an
outside party (`eve`) by any route. The point: no matter what the model says or
tries, governance + structural isolation hold. The LLM is the fuzzer;
`treasury-desk/invariants.py` is the deterministic oracle.

```sh
export ANTHROPIC_API_KEY=sk-...
.venv/bin/python treasury-desk/adversarial.py --turns 4 --attacker david
```

The printed transcript shows David's exfiltration attempts hitting `DENY`, a
final `Eve received: 0 cents`, and `invariants: PASS`. `test_adversarial.py`
asserts those safety properties and is skipped automatically without a key.

## 5. Recall

Past runs live under `runs/<timestamp>/`; open `summary.md` for the verdict and
the `NN-*.log` files for full transcripts (including the adversarial
conversation). `runs/latest` always points at the newest. Because `runs/` is
gitignored, these accumulate locally without bloating the repo; copy any run dir
elsewhere (or into `runs/sample-run/`) if you want it committed.
