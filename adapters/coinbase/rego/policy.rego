# The same business rules as policy.yaml, expressed in Rego — the policy
# language of Open Policy Agent (OPA), the industry-standard policy-as-code
# engine.
#
# Input document shape (mirrors what policy_engine.py's _build_context builds):
#
#   {
#     "operation": "create_order",
#     "params":  {"product_id": "BTC-USD", "side": "BUY", "quote_size": "15.00"},
#     "derived": {"notional_usd": 15.0,
#                 "daily_notional_after": 20.0,
#                 "daily_order_count": 1},
#     "approval": {"valid": false}
#   }
#
# The host application still owns the platform controls (kill switch, rate
# limit, HMAC token verification) and feeds their results in — here, the
# outcome of token verification arrives as input.approval.valid. OPA judges;
# it does not execute.

package coinbase.governance

import rego.v1

allowed_operations := {
	"get_accounts",
	"get_account",
	"get_product",
	"get_market_trades",
	"create_order",
}

forbidden_operations := {
	"send",
	"withdraw",
	"create_transfer",
	"update_account",
	"delete_account",
}

allowed_products := {"BTC-USD", "ETH-USD"}

per_order_cap_usd := 25.0

daily_notional_cap_usd := 100.0

max_orders_per_day := 10

approval_threshold_usd := 10.0

# --------------------------------------------------------------------- #
# Deny rules. Each contributes a human-readable reason.
# --------------------------------------------------------------------- #

deny contains msg if {
	input.operation in forbidden_operations
	msg := sprintf("'%s' is forbidden", [input.operation])
}

deny contains msg if {
	not input.operation in allowed_operations
	msg := sprintf("'%s' not in allowed_operations", [input.operation])
}

deny contains msg if {
	input.operation == "create_order"
	not input.params.product_id in allowed_products
	msg := sprintf("product '%s' not in allowed_products", [input.params.product_id])
}

# NOTE the `not <positive condition>` form below: in Rego, a comparison
# against a MISSING field is undefined (not false), so a rule written as
# `input.derived.side != "BUY"` would silently fail OPEN when `side` is
# absent. `not input.derived.side == "BUY"` fails CLOSED, matching the
# Python engine's behavior.

deny contains msg if {
	input.operation == "create_order"
	not input.derived.side == "BUY"
	msg := "side must be BUY (accumulate-only agent)"
}

deny contains msg if {
	input.operation == "create_order"
	not input.derived.notional_usd > 0
	msg := "order notional must be positive"
}

deny contains msg if {
	input.operation == "create_order"
	not input.derived.notional_usd <= per_order_cap_usd
	msg := sprintf("per-order cap $%.2f exceeded", [per_order_cap_usd])
}

deny contains msg if {
	input.operation == "create_order"
	not input.derived.daily_notional_after <= daily_notional_cap_usd
	msg := sprintf("daily notional cap $%.2f exceeded", [daily_notional_cap_usd])
}

deny contains msg if {
	input.operation == "create_order"
	not input.derived.daily_order_count < max_orders_per_day
	msg := sprintf("daily order count cap (%d) reached", [max_orders_per_day])
}

# --------------------------------------------------------------------- #
# Approval gate: over-threshold orders pass only with a verified token.
# --------------------------------------------------------------------- #

needs_approval contains msg if {
	input.operation == "create_order"
	input.derived.notional_usd > approval_threshold_usd
	not approval_ok
	msg := sprintf("order $%.2f > $%.2f requires human approval", [input.derived.notional_usd, approval_threshold_usd])
}

approval_ok if input.approval.valid == true

# --------------------------------------------------------------------- #
# Final verdict: DENY > NEEDS_APPROVAL > ALLOW, same as the Python engine.
# --------------------------------------------------------------------- #

default verdict := "ALLOW"

verdict := "DENY" if count(deny) > 0

verdict := "NEEDS_APPROVAL" if {
	count(deny) == 0
	count(needs_approval) > 0
}

decision := {
	"verdict": verdict,
	"deny_reasons": deny,
	"approval_reasons": needs_approval,
}
