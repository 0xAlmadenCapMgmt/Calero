# Native OPA unit tests for policy.rego, mirroring a few of the pytest cases.
# Run with:  opa test rego/policy.rego rego/policy_test.rego -v

package coinbase.governance_test

import data.coinbase.governance
import rego.v1

small_buy := {
	"operation": "create_order",
	"params": {"product_id": "BTC-USD", "side": "BUY", "quote_size": "5.00"},
	"derived": {"notional_usd": 5.0, "side": "BUY", "daily_notional_after": 5.0, "daily_order_count": 0},
	"approval": {"valid": false},
}

test_small_buy_allowed if {
	governance.verdict == "ALLOW" with input as small_buy
}

test_read_op_allowed if {
	governance.verdict == "ALLOW" with input as {"operation": "get_accounts"}
}

test_forbidden_op_denied if {
	governance.verdict == "DENY" with input as {"operation": "send"}
}

test_unknown_op_denied_by_default if {
	governance.verdict == "DENY" with input as {"operation": "delete_everything"}
}

test_sell_denied if {
	governance.verdict == "DENY" with input as object.union(
		small_buy,
		{"derived": {"notional_usd": 5.0, "side": "SELL", "daily_notional_after": 5.0, "daily_order_count": 0}},
	)
}

test_per_order_cap if {
	governance.verdict == "DENY" with input as object.union(
		small_buy,
		{"derived": {"notional_usd": 50.0, "side": "BUY", "daily_notional_after": 50.0, "daily_order_count": 0}},
	)
}

test_over_threshold_needs_approval if {
	governance.verdict == "NEEDS_APPROVAL" with input as object.union(
		small_buy,
		{"derived": {"notional_usd": 15.0, "side": "BUY", "daily_notional_after": 15.0, "daily_order_count": 0}},
	)
}

test_missing_side_fails_closed if {
	governance.verdict == "DENY" with input as json.remove(small_buy, {"/derived/side"})
}

test_over_threshold_with_valid_approval if {
	governance.verdict == "ALLOW" with input as object.union(
		small_buy,
		{
			"derived": {"notional_usd": 15.0, "side": "BUY", "daily_notional_after": 15.0, "daily_order_count": 0},
			"approval": {"valid": true},
		},
	)
}
