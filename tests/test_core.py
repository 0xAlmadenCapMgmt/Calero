"""Tests for the platform-agnostic core: platform controls, the approval-token
lifecycle, and rules over params.* judged by the default NullAdapter.

No adapter is constructed anywhere in this file — that is the point. The core
governs a made-up `create_thing` operation with a params-only policy, proving
the engine is a general Policy Decision Point with no domain code. Adapter-
specific derivation is covered in test_coinbase_adapter.py / test_payments_adapter.py.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import PolicyEngine, Request, Verdict  # noqa: E402

SECRET = "test-secret"


def base_policy(tmp_path):
    """A params-only policy — every rule references params.*, so the default
    NullAdapter (no derived facts) is enough to judge it."""
    return {
        "version": 2,
        "agent_id": "core-test",
        "enabled": True,
        "kill_switch_file": str(tmp_path / "KILL"),
        "allowed_operations": ["ping", "create_thing"],
        "forbidden_operations": ["nuke"],
        "rules": [
            {
                "id": "allowed-color",
                "applies_to": "create_thing",
                "description": "Only approved colors",
                "check": {
                    "field": "params.color",
                    "op": "in",
                    "value": ["red", "blue"],
                },
                "on_fail": "deny",
            },
            {
                "id": "approval-threshold",
                "applies_to": "create_thing",
                "description": "Amount over 10 needs approval",
                "check": {"field": "params.amount", "op": "<=", "value": 10},
                "on_fail": "needs_approval",
            },
        ],
        "approvals": {"token_ttl_seconds": 900},
        "rate_limit": {"max_requests_per_minute": 1000},
        "audit": {"log_file": str(tmp_path / "audit.jsonl")},
    }


@pytest.fixture
def engine(tmp_path):
    # No adapter passed -> NullAdapter. Core stands alone.
    return PolicyEngine(base_policy(tmp_path), approval_secret=SECRET)


def thing(amount, color="red", token=None):
    return Request(
        "create_thing", {"color": color, "amount": amount}, approval_token=token
    )


# --------------------------------------------------------------------- #
#  Platform controls
# --------------------------------------------------------------------- #

def test_allowed_op(engine):
    assert engine.evaluate(Request("ping")).verdict is Verdict.ALLOW


def test_forbidden_op_denied(engine):
    d = engine.evaluate(Request("nuke"))
    assert d.verdict is Verdict.DENY
    assert "forbidden" in d.reason


def test_unknown_op_denied_by_default(engine):
    d = engine.evaluate(Request("delete_everything"))
    assert d.verdict is Verdict.DENY
    assert "allowed_operations" in d.reason


def test_forbidden_beats_allowed(tmp_path):
    policy = base_policy(tmp_path)
    policy["allowed_operations"].append("nuke")
    eng = PolicyEngine(policy, approval_secret=SECRET)
    assert eng.evaluate(Request("nuke")).verdict is Verdict.DENY


def test_kill_switch(engine, tmp_path):
    (tmp_path / "KILL").touch()
    d = engine.evaluate(Request("ping"))
    assert d.verdict is Verdict.DENY
    assert "kill switch" in d.reason


def test_disabled_policy(tmp_path):
    eng = PolicyEngine(
        base_policy(tmp_path), approval_secret=SECRET, overrides={"enabled": False}
    )
    assert eng.evaluate(Request("ping")).verdict is Verdict.DENY


def test_rate_limit(tmp_path):
    eng = PolicyEngine(
        base_policy(tmp_path),
        approval_secret=SECRET,
        overrides={"rate_limit": {"max_requests_per_minute": 3}},
    )
    for _ in range(3):
        assert eng.evaluate(Request("ping")).verdict is Verdict.ALLOW
    d = eng.evaluate(Request("ping"))
    assert d.verdict is Verdict.DENY
    assert "rate limit" in d.reason


# --------------------------------------------------------------------- #
#  Params-only rules under the NullAdapter (the "core stands alone" claim)
# --------------------------------------------------------------------- #

def test_params_rule_allows(engine):
    assert engine.evaluate(thing(5, color="blue")).verdict is Verdict.ALLOW


def test_params_rule_denies(engine):
    d = engine.evaluate(thing(5, color="green"))
    assert d.verdict is Verdict.DENY
    assert "allowed-color" in d.failed_rules


def test_missing_derived_field_fails_closed(tmp_path):
    """A rule referencing derived.* with the NullAdapter can never pass —
    there are no derived facts, so it fails closed."""
    policy = base_policy(tmp_path)
    policy["rules"].append(
        {
            "id": "needs-derived",
            "applies_to": "create_thing",
            "description": "References a fact the NullAdapter never supplies",
            "check": {"field": "derived.anything", "op": ">", "value": 0},
            "on_fail": "deny",
        }
    )
    eng = PolicyEngine(policy, approval_secret=SECRET)
    d = eng.evaluate(thing(5))
    assert d.verdict is Verdict.DENY
    assert "needs-derived" in d.failed_rules


# --------------------------------------------------------------------- #
#  Human approval flow (generic — signs over operation + params)
# --------------------------------------------------------------------- #

def test_over_threshold_needs_approval(engine):
    d = engine.evaluate(thing(15))
    assert d.verdict is Verdict.NEEDS_APPROVAL
    assert "approval-threshold" in d.failed_rules


def test_valid_token_allows(engine):
    req = thing(15)
    token = engine.mint_approval_token(req.operation, req.params)
    assert engine.evaluate(thing(15, token=token)).verdict is Verdict.ALLOW


def test_token_bound_to_exact_params(engine):
    req = thing(15)
    token = engine.mint_approval_token(req.operation, req.params)
    d = engine.evaluate(thing(20, token=token))   # different amount
    assert d.verdict is Verdict.DENY
    assert "invalid signature" in d.reason


def test_token_single_use(engine):
    req = thing(15)
    token = engine.mint_approval_token(req.operation, req.params)
    assert engine.evaluate(thing(15, token=token)).verdict is Verdict.ALLOW
    d = engine.evaluate(thing(15, token=token))
    assert d.verdict is Verdict.DENY
    assert "already used" in d.reason


def test_expired_token(engine):
    req = thing(15)
    token = engine.mint_approval_token(req.operation, req.params, ttl_seconds=-1)
    d = engine.evaluate(thing(15, token=token))
    assert d.verdict is Verdict.NEEDS_APPROVAL
    assert "expired" in d.reason


def test_garbage_token_denied(engine):
    d = engine.evaluate(thing(15, token="not-a-real-token"))
    assert d.verdict is Verdict.DENY
    assert "invalid signature" in d.reason


# --------------------------------------------------------------------- #
#  Audit log
# --------------------------------------------------------------------- #

def test_every_evaluation_audited(engine, tmp_path):
    engine.evaluate(Request("ping"))
    engine.evaluate(Request("nuke"))
    engine.evaluate(thing(5, color="green"))
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
