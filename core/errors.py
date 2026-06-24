"""Domain exceptions.

Kept in one place so the meaning of each failure is explicit and greppable.
Only the exceptions used by the Phase-1 fixes (01-05) live here for now; more
will be added as later phases need them (no premature error taxonomy).
"""


class ProcureOSError(Exception):
    """Base class for all ProcureOS domain errors."""


# ── Fix 01 — payment idempotency ────────────────────────────────────────────
class PaymentDuplicateError(ProcureOSError):
    """A Volopay call returned 'duplicate' but the prior payment is not in a
    known-settled state. Never auto-retry on this — a human must resolve it."""


# ── Fix 02 — atomic budget re-check ─────────────────────────────────────────
class BudgetLockError(ProcureOSError):
    """Could not acquire the per-(company, category) budget lock in time —
    another payment for the same budget is in flight. Caller should retry."""


class BudgetExceededError(ProcureOSError):
    """The category budget cannot cover this amount at payment time."""


# ── LLM layer (agents/llm_router.py) ────────────────────────────────────────
class LLMError(ProcureOSError):
    """Base class for LLM-routing failures."""


class RateLimitError(LLMError):
    """A provider returned a rate-limit error for a specific model. The router
    moves on to the next model in the task's fallback chain."""


class ProviderDownError(LLMError):
    """A provider call failed (timeout / 5xx / connection). Counts toward the
    provider's circuit breaker."""


class LLMParseError(LLMError):
    """The model's response was not valid JSON after fence-stripping."""


class AllModelsFailedError(LLMError):
    """Every model in a task's routing chain failed or was filtered out."""


class QuoteAmbiguousError(LLMError):
    """Quote parsing could not produce a confident structured price (Fix 13).
    The goal routes to the human-operator queue rather than showing the
    approver an uncertain quote."""


# ── Approval flow (core/approval_manager.py, api/routes/approvals.py) ────────
class StateConflictError(ProcureOSError):
    """The goal is not in the state this action requires (maps to HTTP 409)."""


class OptionNotFoundError(ProcureOSError):
    """The approver chose an option id not present on the goal (HTTP 404/422)."""


class ApprovalTokenError(ProcureOSError):
    """A magic-link approval token is invalid, expired, or already used
    (Fix 12) — maps to HTTP 410."""


class SlackSignatureError(ProcureOSError):
    """Slack request signature failed HMAC verification (Fix 11) — HTTP 401."""
