# A Security Primer for Agent Governance Layers

This document is use-case agnostic. The examples reference this repository's
Coinbase trading layer because it is concrete and nearby, but every principle
here applies equally to an agent that pays bills, moves money between
accounts, files tickets, sends email, or deploys code. If an autonomous
system holds a credential and takes actions, this is the threat model it
lives in.

## 1. Why agents change the security problem

Traditional application security assumes the code's *intent* is fixed at
review time: you audit what the program does, and it keeps doing that. An
LLM-driven agent breaks that assumption in three ways.

First, **the agent's behavior is not fixed**. It is sampled from a model at
runtime, steered by a prompt, and can differ between runs on identical
inputs. You cannot review its intent; you can only bound its authority.

Second, **the agent's inputs are attack surface**. Anything the agent reads —
a web page, an email, a document, an API response, a customer message — can
contain instructions that the model may follow (*prompt injection*). An agent
that reads untrusted content must be treated as if an attacker can sometimes
choose its actions. This is a classic *confused deputy* problem: the attacker
has no credentials, but the agent does, and the attacker steers the agent.

Third, **agents concentrate credentials**. To be useful, an agent holds keys
that can move money or change systems, and it runs unattended at machine
speed. A mistake or a hijack does not produce one bad action; it produces
bad actions in a loop until something stops it.

The consequence: **you must assume the agent is untrusted code**, even when
you wrote its prompt and mean well. The security of the system cannot depend
on the agent choosing correctly. It must depend on what the agent is
*structurally able* to do. That is what a governance layer is for.

## 2. The reference architecture

```
            untrusted zone           │            trusted zone
                                     │
  ┌─────────┐   proposed action      │   ┌──────────────┐     policy files
  │  Agent  │ ──────────────────────────▶│  Enforcement  │◀── (versioned,
  └─────────┘                        │   │  point (PEP)  │     reviewed,
       ▲                             │   └──────┬───────┘     tested)
       │ tool results only           │          │ request + context
       │                             │          ▼
       │                             │   ┌──────────────┐
       │                             │   │ Decision      │──▶ audit log
       │                             │   │ point (PDP)   │    (append-only)
       │                             │   └──────┬───────┘
       │                             │          │ ALLOW only
       │                             │          ▼
       │                             │   ┌──────────────┐
       └─────────────────────────────────│ Real system   │ (holds the
                                     │   │ (bank, API…)  │  credential)
                                     │   └──────────────┘
```

Three components, with names from the access-control literature:

- The **Policy Enforcement Point (PEP)** is the *only* gateway between the
  agent and the real system. In this repo it is `GovernedClient`. The
  credential lives here (or beyond here), never in the agent.
- The **Policy Decision Point (PDP)** judges each proposed action against
  versioned policy and returns ALLOW / DENY / NEEDS_APPROVAL with reasons.
  In this repo it is `PolicyEngine`; at scale it is often OPA or similar.
- The **audit log** records every proposal and every verdict, allowed or not.

The dashed line matters more than the boxes: everything the agent touches is
the untrusted zone. The governance layer is trusted, so it must be small,
boring, deterministic, and testable — the opposite of the agent.

## 3. Threat model

Design against these adversaries, roughly in order of likelihood:

1. **The agent itself, malfunctioning.** Hallucinated parameters, retry
   loops, misread instructions, plausible-but-wrong actions. No attacker
   required; this is the common case.
2. **An external attacker steering the agent** via prompt injection in any
   content the agent reads. Assume they can choose the agent's proposed
   actions but cannot touch the governance layer's host or secrets.
3. **A replay or stockpiling attacker** who captures an approved action or
   approval artifact and reuses it — including the agent itself re-submitting
   an approval it was granted once.
4. **An attacker with partial host access** who can restart the agent
   process (wiping in-memory state), edit files the agent's user can write,
   or read the agent's environment.
5. **Operator error**: a human weakening a policy by accident, approving the
   wrong thing, or leaving a stale credential over-scoped.

Explicitly out of scope for the governance layer (handle elsewhere): full
compromise of the trusted host, a malicious insider holding the approval
secret, and vulnerabilities in the upstream provider. A governance layer
bounds an agent; it does not defend the machine it runs on.

## 4. Core principles

### 4.1 Structural enforcement, not behavioral trust

The single most important property. The agent must be *unable* to bypass the
layer, not *instructed* not to. Concretely: the agent's process or context
never contains the real credential or a raw client; it holds only a wrapper
whose every method routes through the PDP. Prompt rules ("never withdraw")
are worthless as a control — the model that ignored one instruction will
ignore yours. If there exists any code path from agent to system that skips
the policy check, the layer is decorative.

Test for it: assume the agent will *try* to bypass. In this repo,
`demo.py` scenario 8 calls `send` directly — the only reason it fails is
that no unguarded path exists.

### 4.2 Deny by default, fail closed

Every dimension of the policy should be an allowlist: operations not listed
are denied, payees/products not listed are denied, and — easy to miss — a
rule that *cannot be evaluated* is a failed rule. Missing field, unparseable
amount, unknown operator, malformed context: deny. Attackers live in the
gaps between "true" and "false"; do not let "undefined" be a third state
that passes. (The Rego appendix documents a real instance: in Rego, a
comparison against a missing field is undefined rather than false, so a
naively written rule fails *open* when a field is absent.)

### 4.3 Least privilege, in depth

The governance layer is one wall of several, and the outer walls are the
provider's own controls: API key scopes, per-key withdrawal bans, IP
allowlists, account-level limits. Scope the credential to the minimum the
agent's job needs — a key that cannot withdraw beats any policy that says
"do not withdraw." The governance layer then adds the fine-grained rules the
provider cannot express (dollar caps, approval workflows, business hours).
Neither layer substitutes for the other; they fail independently.

### 4.4 Bound the blast radius

Because an agent errs *repeatedly*, per-action checks are not enough. Layer
cumulative limits so that the worst uninterrupted failure is an acceptable
loss: per-action caps, rolling daily/weekly spend caps, action-count caps,
and rate limits. Choose the numbers by asking "what total damage am I
willing to discover tomorrow morning?" — not by what the agent normally
needs. Velocity limits are the difference between an incident and a
disaster.

### 4.5 Human-in-the-loop with integrity

An approval workflow is itself attack surface. A `y/n` prompt or a boolean
flag can be replayed, forged, or socially engineered. An approval artifact
should have all of these properties:

- **Bound to the exact action** — signed over the operation *and* its full
  parameters, so approving a $15 payment approves nothing else. (HMAC with
  a secret the agent does not hold, as in `mint_approval_token`.)
- **Expiring** — a short TTL bounds the window between human intent and
  execution.
- **Single-use** — the nonce is consumed on execution; replay is denied.
- **Minted out-of-band** — the approving human acts through a channel the
  agent cannot write to, and the signing secret never enters the agent's
  environment.

And one procedural rule: the approval request shown to the human must be
rendered from the *parameters the PDP will enforce*, never from text the
agent composed — otherwise the agent (or its injector) describes a benign
action and executes a different one.

### 4.6 Protect the policy itself

If the agent can edit the policy file, every other control is theater. The
policy must be owned by a different user than the agent process, read-only
to the agent, version-controlled, and changed only through review. Treat a
policy change with the seriousness of a code deploy: PR review, CI tests
that assert every guardrail's verdict (this repo's `tests/`), and an audit
trail of who changed what. At scale, signed policy bundles distributed from
a central server (as OPA supports) make the supply chain explicit.

### 4.7 State is part of the security boundary

Cumulative limits and single-use tokens only work if their state survives.
In-memory counters reset on restart — which means "restart the process" is
an attack that refills the daily budget and re-opens the replay window (a
known limitation of this repo's demo, called out in its README). Production
state (spend counters, consumed nonces, rate windows) belongs in durable
storage that the agent cannot write to directly. More generally, beware
**TOCTOU** (time-of-check to time-of-use) gaps: the balance or total you
checked must be the one that holds when the action executes — evaluate and
commit under a lock or transaction, and commit state only on ALLOW so denied
attempts don't consume budget.

### 4.8 Validate and canonicalize inputs

The PDP judges the parameters it is shown, so the parameters must be parsed
defensively: amounts as validated decimals (reject `"1e9"`, negative values,
NaN, unit ambiguity), identifiers checked against strict formats, unknown or
extra parameters rejected rather than passed through to the real system.
Sign and compare *canonical* serializations (sorted keys, fixed encodings)
so that two spellings of the same request cannot have different identities.
Everything the agent submits is attacker-controlled input; treat it with
web-form levels of suspicion.

### 4.9 Audit everything, tamper-evidently

Log every proposal — allowed, denied, and escalated — with timestamp, agent
identity, full parameters, verdict, and the specific rules that fired.
Denied attempts are the most valuable entries: a burst of denials is your
earliest hijack signal. The log must be append-only and shipped off-host
promptly (an attacker who owns the box edits local logs); hash-chaining
entries adds tamper-evidence. An unauditable governance layer cannot answer
the only question that matters after an incident: *what exactly did the
agent try to do, and when?*

### 4.10 A kill switch a human can reach

When something looks wrong, the response time must be seconds, not a deploy
cycle. Provide a big red switch that denies everything and requires no code
change: a sentinel file (`touch /tmp/agent-KILL` in this repo), a feature
flag, a revoked credential. It should fail closed (if the switch's state
cannot be read, deny), be reachable by an on-call human without special
tooling, and be tested regularly — an untested kill switch is a hope, not a
control.

## 5. Attacks mapped to controls

| Attack | Primary control | Backstop |
|---|---|---|
| Prompt injection steers the agent to withdraw funds | Structural enforcement + forbidden-operation list (§4.1, §4.2) | Credential lacks withdrawal scope (§4.3) |
| Agent loops, submitting the same payment repeatedly | Velocity caps and daily limits (§4.4) | Rate limit; anomaly alerts on audit log (§4.9) |
| Agent replays a captured approval token | Single-use nonce (§4.5) | Token TTL |
| Agent hoards an approval for later misuse | TTL expiry (§4.5) | Parameter binding limits what it's good for |
| Approval requested for action A, action B executed | Sign over exact canonical parameters (§4.5, §4.8) | Human sees PDP-rendered request, not agent prose |
| Process restart wipes counters, budget refills | Durable state store (§4.7) | Off-host audit reveals the restart pattern |
| Agent edits policy file to allow everything | File ownership/permissions, reviewed deploys (§4.6) | Policy CI tests fail on weakened guardrails |
| Malformed amount ("1e9", "-5", "lots") slips past a cap | Fail-closed parsing and strict validation (§4.2, §4.8) | Per-day cumulative cap bounds the damage |
| Ongoing incident, cause unknown | Kill switch (§4.10) | Credential revocation at the provider |

## 6. What a governance layer cannot do

Honesty about limits is part of the security posture. This layer does not
protect against: an attacker with root on the trusted host (they can patch
the PEP); a holder of the approval secret acting maliciously; a provider-side
vulnerability; or an approver who confirms a harmful action they were
socially engineered into reading carelessly. It also cannot make a bad
policy good — a $1,000,000 per-order cap is enforced perfectly and protects
nothing. The layer converts "the agent can do anything the key allows" into
"the agent can do at most what the policy says"; choosing a safe policy
remains a human judgment.

## 7. Review checklist

Before trusting any agent governance layer, verify:

- [ ] No code path from agent to credential/system bypasses the PEP
- [ ] Raw credentials and approval secrets never appear in the agent's
      environment, context window, or logs
- [ ] Every list is an allowlist; unevaluatable rules deny (fail closed)
- [ ] Upstream credential is scoped to the minimum (no withdrawal/transfer
      permission it doesn't need)
- [ ] Per-action, cumulative (daily/weekly), and count/rate limits all exist,
      sized to acceptable worst-case loss
- [ ] Approvals are parameter-bound, expiring, single-use, minted out-of-band
- [ ] Approval prompts render PDP-enforced parameters, not agent-authored text
- [ ] Policy files are read-only to the agent, version-controlled, and
      guarded by CI tests asserting each rule's verdict
- [ ] Counters, nonces, and rate windows survive process restarts
- [ ] Parameters are canonicalized and strictly validated before judgment
- [ ] Audit log is append-only, includes denials, and ships off-host
- [ ] Kill switch exists, fails closed, and has actually been tested
