# Policy-as-Code Governance Layer for a Coinbase Agent

This project is a governance layer that sits between an AI agent (or any automated script) and the Coinbase API. Its purpose is to guarantee that the agent can only do what a human has explicitly permitted, no matter what the agent decides to attempt. The rules live in a plain-text policy file rather than being scattered through application code, which is what "policy as code" means: the policy is versionable, reviewable, testable, and changeable without touching the program logic.

The design follows three principles. First, deny by default: any operation not explicitly allowed is blocked, and a rule that cannot be evaluated (missing field, unknown operator) counts as failed. Second, structural enforcement: the agent is never given the raw Coinbase client or the API key, only a wrapper that routes every call through the policy engine, so bypassing governance is not merely forbidden but impossible from the agent's position. Third, total auditability: every request, whether allowed or denied, is written to an append-only log with a timestamp, the parameters, the verdict, the reason, and the ids of any rules that failed.

## Configuration as Code vs Policy as Code

An earlier version of this project kept the rule *logic* in Python and only the *numbers* (caps, allowlists) in YAML. That is configuration as code — useful, but changing what kinds of rules exist still meant editing the engine. This version moves the business rules themselves into `policy.yaml` as declarative data (a field, an operator, a value, and a consequence), evaluated generically by the engine. Adding a brand-new guardrail — say, a per-product daily cap — is now a policy-file edit plus a test, with no engine changes. That is the maturity step the industry standard tools (OPA, Cedar) institutionalize; see `rego/` for the same policy expressed in OPA's Rego language.

Two layers remain in the policy file, and the distinction is deliberate:

- **Platform controls** — kill switch, master enable, operation allowlist/forbidden list, rate limit, active hours. These guard the pipeline itself and are implemented in the engine, parameterized by the YAML.
- **Business rules** (`rules:`) — the declarative field/op/value checks that judge each request against a context document of submitted parameters (`params.*`) and engine-computed facts (`derived.*`, e.g. parsed order notional and running daily totals). Each rule fails to either `deny` or `needs_approval`; deny always outranks approval.

## The Files

### SECURITY_PRIMER.md

A use-case-agnostic security primer for agent governance layers: the threat model (malfunctioning agents, prompt injection, replay, restart attacks, operator error), the core principles (structural enforcement, fail-closed evaluation, blast-radius limits, approval integrity, auditability, kill switches), a table mapping attacks to controls, and a review checklist. The Coinbase layer in this repo is the running example, but the document stands alone.

### policy.yaml

The heart of the system and the only file that needs to change when the rules change. It declares the platform controls (which operations exist at all, which are permanently forbidden — withdrawals and on-chain sends — the rate limit, the UTC active-hours window, and a kill-switch file path: if that file exists on disk, every request is denied, so a human can freeze the agent instantly with `touch /tmp/coinbase-agent-KILL`). Below those sit the business rules: product allowlist, buy-only constraint, a per-order dollar cap, a rolling daily spend cap, a daily order count cap, and a threshold above which a human must approve each individual order.

### policy_engine.py

The judge (in the literature: the Policy Decision Point). It loads `policy.yaml` and exposes `evaluate(request)`, which builds a context document for the request, runs every applicable rule against it, and returns ALLOW, DENY, or NEEDS_APPROVAL with a human-readable reason and the failed rule ids. Platform controls run first (kill switch, enable flag, forbidden list beating the allowed list, deny-by-default allowlist, active hours, rate limit), then the declarative business rules. Daily spend and order counts are tracked in memory and only committed when a request is actually allowed, so denied attempts never consume budget.

The engine also implements the human approval mechanism. When an order trips a `needs_approval` rule, the request halts with NEEDS_APPROVAL. A human then runs `mint_approval_token(operation, params)` out-of-band, producing a token of the form `nonce.expiry.signature`, HMAC-signed over the exact operation, parameters, nonce, and expiry using a secret the agent does not know (`AGENT_APPROVAL_SECRET`). The token is valid only for that precise request (change the amount or product and the signature no longer matches), expires after a TTL (default 15 minutes, configurable under `approvals:`), and is single-use — the engine records the nonce when the approved order executes and denies any replay. The consumed-nonce set lives in memory, with the same restart caveat as the counters (see Limitations).

### governed_client.py

The enforcement point (the Policy Enforcement Point). `GovernedClient` wraps the real Coinbase client and is the only object the agent is ever handed. Its single method, `call(operation, params, approval_token)`, submits the request to the policy engine and only invokes the underlying client method if the verdict is ALLOW. A DENY raises `PolicyViolation`; a NEEDS_APPROVAL raises `ApprovalRequired`, which the surrounding application should surface to a human. Because the raw client and the API key never enter the agent's context, there is no code path from the agent to Coinbase that skips the policy check.

### demo.py

A runnable demonstration using a mock Coinbase client, so it executes safely with no API key and no network access. It walks through nine scenarios: a permitted balance read, a small buy within limits, a buy over the per-order cap (denied), a mid-size buy that triggers the approval gate, the same buy succeeding with a valid minted token, the same token replayed (denied — single-use), a sell attempt (denied by the buy-only rule), a withdrawal attempt (denied by the forbidden list), and an unknown operation (denied by default). It finishes by printing the tail of the audit log. The demo removes the active-hours rule via an in-memory policy override so the other rules can be exercised at any time of day; `policy.yaml` itself is untouched.

### tests/test_policy_engine.py

The other half of the policy-as-code pitch: because policies are data judged by a generic engine, every guardrail can be regression-tested. The suite asserts the verdict for each platform control and business rule, the full approval-token lifecycle (missing, valid, wrong-params, replayed, expired, garbage), fail-closed behavior on unparseable order sizes, that denied orders do not consume daily budget, and that every evaluation lands in the audit log. In a real deployment this runs in CI, so a policy edit that accidentally weakens a guardrail fails the build before it reaches the agent.

### alice-bob/

A companion learning project: two Claude-backed personas (Alice and Bob) hold a conversation about checking, savings, and stock investments. The agents have no tools and no access to any financial system — they can only talk — and each money movement they propose is emitted as a structured intent, mapped to this project's `Request(operation, params)` shape, and judged live by the `PolicyEngine` against `policy.yaml` (Bob's $500 NVDA idea gets denied by the product allowlist; his $5 bitcoin buy is allowed; a $15 one needs human approval). Verdicts are printed and audited but nothing ever executes. Has its own README, requirements, and tests.

### rego/

The same business rules re-implemented in Rego for Open Policy Agent, with native `opa test` unit tests, sample inputs for `opa eval`, and a README comparing the two forms and describing when to graduate from a homegrown engine to OPA. Nothing in the main project requires the `opa` binary.

## Running It

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # pyyaml, pytest

.venv/bin/python demo.py                    # the nine scenarios
.venv/bin/python -m pytest tests/ -v        # the policy test suite
```

To connect it to the real Coinbase API, install `coinbase-advanced-py`, construct the official `RESTClient` with your API credentials, and pass it to `GovernedClient` in place of the mock. Everything else stays the same.

## How This Layers with Coinbase's Own Controls

This layer is defense-in-depth, not a replacement for API key scopes. The key's permissions, enforced by Coinbase's servers, are the outer wall: a read-only key cannot trade no matter what any local code says. This governance layer adds the fine-grained rules Coinbase cannot express — dollar caps, buy-only constraints, approval workflows, trading hours — plus a complete audit trail of everything the agent attempted. The recommended posture is to use the narrowest key scope the agent's job requires and this policy layer on top of it, and never to grant transfer or withdrawal permission on the key at all.

## Limitations Worth Knowing

Daily spend counters, order counts, and the consumed-nonce set for approval tokens are held in memory, so they reset if the process restarts; a production deployment should persist them to disk or a database (a restart currently re-opens the replay window for unexpired tokens). The engine trusts the parameters it is shown, so the wrapper must remain the sole gateway to the client. The policy file itself must be protected — if the agent can edit `policy.yaml`, the governance is decorative — so in production, make the file read-only to the agent process and owned by a different user. Smaller production-hardening items in the same spirit: the active-hours window does not support ranges that wrap past midnight UTC; requests denied by business rules still consume a rate-limit slot; and audit entries are plain JSONL lines without hash chaining, so log tampering is detectable only by external means (ship them off-host).
