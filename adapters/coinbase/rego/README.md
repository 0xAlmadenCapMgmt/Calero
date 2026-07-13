# Appendix: The Same Policy in OPA / Rego

The main project implements policy-as-code with a small homegrown engine and
YAML rules. That is the right way to *learn* the concepts, but it is not what
you would deploy at scale. The industry standard is **Open Policy Agent
(OPA)** — a CNCF-graduated, general-purpose policy engine — and its policy
language, **Rego**. This directory re-implements the business rules from
`../policy.yaml` in Rego so you can compare the two forms directly.

## The same rule, side by side

The per-order cap in our YAML dialect:

```yaml
- id: per-order-cap
  applies_to: create_order
  description: No single order above $25
  check: {field: derived.notional_usd, op: "<=", value: 25.00}
  on_fail: deny
```

And in Rego (from `policy.rego`):

```rego
deny contains msg if {
    input.operation == "create_order"
    not input.derived.notional_usd <= per_order_cap_usd
    msg := sprintf("per-order cap $%.2f exceeded", [per_order_cap_usd])
}
```

Same idea in both: the rule is *data plus a tiny condition*, judged against an
input document, producing a reason. Rego's advantages show up past the toy
stage — arbitrary logic (joins across data, aggregations, regex), a mature
test runner, and one engine reusable for every policy domain (API
authorization, Kubernetes admission, CI checks), not just this agent.

One Rego gotcha worth internalizing (it is called out in `policy.rego`): a
comparison against a **missing field is undefined, not false**. A rule written
`input.derived.side != "BUY"` never fires when `side` is absent — it fails
open. Writing the check as `not input.derived.side == "BUY"` fails closed,
matching the Python engine. Fail-closed defaults are something you must
deliberately design in Rego.

## Division of labor

OPA is a **Policy Decision Point only** — it judges, it never executes. The
host application (the `GovernedClient` equivalent) remains the enforcement
point and still owns everything stateful or cryptographic:

- kill switch, rate limiting, daily counters → computed by the host, passed
  in as `input.derived.*`
- HMAC approval-token verification → done by the host, passed in as
  `input.approval.valid`

This mirrors real deployments: OPA runs as a sidecar or library, the app
gathers context, sends `input`, and enforces the returned decision.

## Running it

Install OPA (single static binary):

```sh
brew install opa        # macOS; see openpolicyagent.org for other platforms
```

Run the unit tests (target the .rego files — pointing `opa test` at the whole
directory would also try to load the JSON input examples as data documents):

```sh
opa test rego/policy.rego rego/policy_test.rego -v
```

Evaluate a sample input:

```sh
opa eval -d rego/policy.rego -i rego/input_examples/small_buy.json \
    'data.coinbase.governance.decision'

opa eval -d rego/policy.rego -i rego/input_examples/over_cap_sell.json \
    'data.coinbase.governance.decision'

opa eval -d rego/policy.rego -i rego/input_examples/needs_approval.json \
    'data.coinbase.governance.decision'
```

Expected verdicts: `ALLOW`, `DENY` (two reasons: SELL side and per-order cap),
and `NEEDS_APPROVAL`.

## When to graduate from the homegrown engine to OPA

Stay homegrown while the rule vocabulary is small and one team owns both the
engine and the policies. Move to OPA when you need any of: rules too complex
for field/op/value triples; multiple services sharing one policy layer;
signed, versioned **policy bundles** pulled from a central server; built-in
**decision logs** shipped to your SIEM; or separation of duties where a
risk/compliance team writes policies without touching application code.
