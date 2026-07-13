"""
The Adapter contract: the seam between the platform-agnostic core and a
specific platform (Coinbase, a payments API, anything else).

The core PolicyEngine knows how to run declarative rules against a context
document of `params` (the request as submitted) and `derived` (facts computed
from it). It does NOT know how to compute those derived facts for any given
platform — that is the one piece of domain knowledge, and it lives here, in an
Adapter.

An adapter supplies exactly two things:

  * derive(request)  -> the `derived.*` facts the policy's rules reference.
                        Must be side-effect free: it is called on every
                        evaluation, including ones that end in DENY.
  * commit(request, context) -> called ONLY after a request is ALLOWed, to
                        advance any stateful accumulators (running daily totals,
                        counts) so later evaluations see the effect.

Because commit runs only on ALLOW, denied attempts never consume budget — the
same guarantee the engine gave before, now expressed generically.

State ownership: an adapter owns its own accumulator state (e.g. per-day
spend). The engine owns only platform-control state (rate-limit timestamps,
consumed approval nonces). derive/commit are invoked while the engine holds its
lock, so an adapter's state is protected — but an adapter instance must belong
to a single PolicyEngine and not be shared across engines.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .policy_engine import Request


@runtime_checkable
class Adapter(Protocol):
    def derive(self, request: "Request") -> dict:
        """Return the derived.* facts for this request (side-effect free)."""
        ...

    def commit(self, request: "Request", context: dict) -> None:
        """Advance domain accumulators after an ALLOW. Default: no-op."""
        ...


class NullAdapter:
    """The trivial adapter: no derived facts, no state.

    Paired with a params-only policy, the core alone is already a working,
    fully generic governance layer — operation allowlist/forbidden list, rate
    limit, active hours, kill switch, and rules over `params.*` plus an
    approval gate. Adapters exist only to add `derived.*` facts on top.
    """

    def derive(self, request: "Request") -> dict:
        return {}

    def commit(self, request: "Request", context: dict) -> None:
        return None
