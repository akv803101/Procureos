"""Multi-model LLM router (PRD Section 26).

Non-negotiable rule: ALL LLM calls go through this module. Each task has a
priority-ordered chain of `provider/model` strings; the router tries them in
order, falling through on provider failure, rate limits, JSON-parse failure, or
(for JSON tasks) a confidence below the gate. Per-provider circuit breakers
(Fix 20) keep a single provider outage from taking the whole system down.

Model IDs: the PRD's routing config and Fix 20 code contained stale Anthropic
IDs (`claude-haiku-4`, `claude-sonnet-4-20250514`). Corrected here against the
claude-api skill (current catalog): Haiku -> `claude-haiku-4-5`,
Sonnet -> `claude-sonnet-4-6` (already correct in the PRD routing table).

Providers are wired lazily and imported inside their handlers, so this module
imports cleanly without any provider SDK installed. Tests inject fake handlers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

from core.config import settings
from core.errors import (
    AllModelsFailedError,
    LLMParseError,
    ProviderDownError,
    QuoteAmbiguousError,
    RateLimitError,
)

log = logging.getLogger(__name__)


# ── Tasks and routing config ────────────────────────────────────────────────
class LLMTask(str, Enum):
    INTENT_PARSING = "intent_parsing"
    QUOTE_PARSING = "quote_parsing"
    OPTION_RANKING = "option_ranking"
    ORCHESTRATION = "orchestration"
    RFQ_GENERATION = "rfq_generation"
    GOAL_REFINEMENT = "goal_refinement"
    ANOMALY_DETECTION = "anomaly_detection"


# Priority order: index 0 is primary, the rest are the fallback chain.
TASK_MODEL_ROUTING: dict[LLMTask, list[str]] = {
    LLMTask.INTENT_PARSING: [
        "groq/llama-3.1-8b-instant",
        "groq/mixtral-8x7b-32768",
        "gemini/gemini-1.5-flash",
        "anthropic/claude-haiku-4-5",   # PRD said claude-haiku-4 (stale) — corrected
    ],
    LLMTask.QUOTE_PARSING: [
        "anthropic/claude-sonnet-4-6",  # primary — handles Hindi/Hinglish best
        "openai/gpt-4o",
        "gemini/gemini-1.5-pro",
        "groq/llama-3.1-70b-versatile", # last resort — watch the confidence gate
    ],
    LLMTask.OPTION_RANKING: [
        "groq/llama-3.1-70b-versatile",
        "gemini/gemini-1.5-flash",
        "anthropic/claude-haiku-4-5",   # corrected from claude-haiku-4
        "openai/gpt-4o-mini",
    ],
    LLMTask.ORCHESTRATION: [
        "anthropic/claude-sonnet-4-6",
        "openai/gpt-4o",
        "gemini/gemini-1.5-pro",
    ],
    LLMTask.RFQ_GENERATION: [
        "groq/llama-3.1-8b-instant",
        "gemini/gemini-1.5-flash",
        "anthropic/claude-haiku-4-5",   # corrected from claude-haiku-4
    ],
    LLMTask.GOAL_REFINEMENT: [
        "groq/llama-3.1-70b-versatile",
        "openai/gpt-4o-mini",
        "anthropic/claude-haiku-4-5",   # corrected from claude-haiku-4
    ],
    LLMTask.ANOMALY_DETECTION: [
        "gemini/gemini-1.5-flash",
        "anthropic/claude-haiku-4-5",   # corrected from claude-haiku-4
        "openai/gpt-4o-mini",
    ],
}


def _resolved_chain(task: LLMTask) -> list[str]:
    """Apply optional per-task env override, making it primary."""
    override = {
        LLMTask.INTENT_PARSING: settings.llm_intent_parser_model,
        LLMTask.QUOTE_PARSING: settings.llm_quote_parser_model,
        LLMTask.ORCHESTRATION: settings.llm_orchestrator_model,
    }.get(task, "")
    default = TASK_MODEL_ROUTING[task]
    if override:
        return [override] + [m for m in default if m != override]
    return default


# ── Results ─────────────────────────────────────────────────────────────────
@dataclass
class RawCompletion:
    """What a provider handler returns."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    confidence: float | None = None
    model: str | None = None
    provider: str | None = None
    fallback_used: bool = False


# ── JSON parsing (prompts.md) ───────────────────────────────────────────────
def safe_parse_json(text: str) -> dict:
    """Strip any markdown fences a model added despite instructions, then parse."""
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?\n?", "", clean)
    clean = re.sub(r"\n?```$", "", clean)
    clean = clean.strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        raise LLMParseError(f"JSON parse failed: {e}. Raw text: {text[:200]}")


# ── Per-provider circuit breaker (Fix 20) ───────────────────────────────────
class CircuitBreaker:
    """Opens after `failure_threshold` consecutive failures; after
    `recovery_timeout` seconds it half-opens (allows one trial). A success
    resets it. `clock` is injectable so tests can control time.
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 120.0, clock=time.monotonic):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self._clock() - self._opened_at >= self.recovery_timeout:
            return False  # half-open — allow a trial call through
        return True

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self._clock()

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None


# Handler signature: async (model_name, prompt, require_json) -> RawCompletion
Handler = Callable[[str, str, bool], Awaitable[RawCompletion]]
# Usage logger signature: async (dict) -> None
UsageLogger = Callable[[dict], Awaitable[None]]


class LLMRouter:
    def __init__(self, *, handlers: dict[str, Handler] | None = None, usage_logger: UsageLogger | None = None):
        self._handlers: dict[str, Handler] = handlers or {
            "anthropic": self._call_anthropic,
            "openai": self._call_openai,
            "gemini": self._call_gemini,
            "groq": self._call_groq,
        }
        self._usage_logger = usage_logger
        self._clients: dict[str, object] = {}
        self._breakers: dict[str, CircuitBreaker] = {
            p: CircuitBreaker() for p in ("anthropic", "openai", "gemini", "groq")
        }

    async def complete(
        self,
        task: LLMTask,
        prompt: str,
        *,
        require_json: bool = True,
        min_confidence: float = 0.85,
        company_id: str | None = None,
        goal_id: str | None = None,
    ) -> LLMResult:
        chain = _resolved_chain(task)
        last_error: Exception | None = None

        for idx, model_string in enumerate(chain):
            provider, model_name = model_string.split("/", 1)
            breaker = self._breakers.get(provider)
            if breaker and breaker.is_open():
                log.warning("[%s] circuit open for %s — skipping %s", task.value, provider, model_string)
                continue
            handler = self._handlers.get(provider)
            if handler is None:
                continue

            fallback_used = idx > 0
            start = time.monotonic()
            try:
                raw = await handler(model_name, prompt, require_json)
            except RateLimitError as e:
                # Rate limits are transient — try the next model, don't trip the breaker.
                log.warning("[%s] rate limited on %s", task.value, model_string)
                last_error = e
                continue
            except Exception as e:  # ProviderDownError or any provider/SDK error
                log.warning("[%s] %s failed: %s", task.value, model_string, e)
                last_error = e
                if breaker:
                    breaker.record_failure()
                continue
            latency_ms = int((time.monotonic() - start) * 1000)

            confidence: float | None = None
            if require_json:
                try:
                    parsed = safe_parse_json(raw.text)
                except LLMParseError as e:
                    log.warning("[%s] %s returned unparseable JSON", task.value, model_string)
                    last_error = e
                    await self._log(task, model_string, company_id, goal_id, raw, latency_ms, False, fallback_used)
                    continue
                confidence = float(parsed.get("confidence", 1.0))

            # The call itself succeeded (provider is healthy) — reset the breaker.
            if breaker:
                breaker.record_success()
            await self._log(task, model_string, company_id, goal_id, raw, latency_ms, True, fallback_used)

            if require_json and confidence is not None and confidence < min_confidence:
                # Confident-enough answer not produced — try a stronger model.
                log.info("[%s] %s confidence %.2f < %.2f — trying next model",
                         task.value, model_string, confidence, min_confidence)
                last_error = LowConfidence(model_string, confidence, min_confidence)
                continue

            return LLMResult(
                text=raw.text, input_tokens=raw.input_tokens, output_tokens=raw.output_tokens,
                confidence=confidence, model=model_string, provider=provider, fallback_used=fallback_used,
            )

        # Chain exhausted. Fix 13: ambiguous quotes go to the operator queue.
        if task == LLMTask.QUOTE_PARSING:
            raise QuoteAmbiguousError(
                f"No confident quote after {len(chain)} models (last: {last_error})"
            )
        raise AllModelsFailedError(f"All models failed for task {task.value}: {last_error}")

    # ── usage logging ───────────────────────────────────────────────────────
    async def _log(self, task, model_string, company_id, goal_id, raw, latency_ms, success, fallback_used):
        if self._usage_logger is None:
            return
        provider, model = model_string.split("/", 1)
        try:
            await self._usage_logger({
                "task": task.value, "provider": provider, "model": model,
                "input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens,
                "latency_ms": latency_ms, "success": success, "fallback_used": fallback_used,
                "company_id": company_id, "goal_id": goal_id,
            })
        except Exception:  # logging must never break the request path
            log.exception("usage logging failed")

    # ── lazy client construction ──────────────────────────────────────────────
    def _client(self, name: str, factory):
        if name not in self._clients:
            self._clients[name] = factory()
        return self._clients[name]

    # ── provider handlers (SDKs imported lazily, gated on keys) ────────────────
    async def _call_anthropic(self, model: str, prompt: str, require_json: bool) -> RawCompletion:
        import anthropic
        client = self._client("anthropic", lambda: anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key))
        try:
            resp = await client.messages.create(
                model=model, max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.RateLimitError as e:
            raise RateLimitError(str(e)) from e
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            raise ProviderDownError(str(e)) from e
        # Iterate for the text block: with thinking off (default) content[0] is
        # text, but a thinking block can lead if thinking is ever enabled —
        # selecting by type is the robust read (claude-api skill).
        # JSON is enforced via the prompt + safe_parse_json (multi-provider).
        # Anthropic's output_config.format could harden this per-provider later.
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return RawCompletion(text=text, input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)

    async def _call_openai(self, model: str, prompt: str, require_json: bool) -> RawCompletion:
        import openai
        client = self._client("openai", lambda: openai.AsyncOpenAI(api_key=settings.openai_api_key))
        kwargs = dict(model=model, max_tokens=1000, messages=[{"role": "user", "content": prompt}])
        if require_json:
            kwargs["response_format"] = {"type": "json_object"}  # OpenAI-native JSON mode
        try:
            resp = await client.chat.completions.create(**kwargs)
        except openai.RateLimitError as e:
            raise RateLimitError(str(e)) from e
        except openai.APIError as e:
            raise ProviderDownError(str(e)) from e
        u = resp.usage
        return RawCompletion(text=resp.choices[0].message.content or "",
                             input_tokens=u.prompt_tokens, output_tokens=u.completion_tokens)

    async def _call_gemini(self, model: str, prompt: str, require_json: bool) -> RawCompletion:
        import google.generativeai as genai
        genai.configure(api_key=settings.google_api_key)  # idempotent
        model_obj = genai.GenerativeModel(model)
        try:
            resp = await asyncio.to_thread(model_obj.generate_content, prompt)
        except Exception as e:
            raise ProviderDownError(str(e)) from e
        # PRD hardcoded 0 tokens for Gemini; read usage_metadata when present so
        # cost tracking doesn't undercount Gemini calls.
        um = getattr(resp, "usage_metadata", None)
        it = getattr(um, "prompt_token_count", 0) if um else 0
        ot = getattr(um, "candidates_token_count", 0) if um else 0
        return RawCompletion(text=getattr(resp, "text", "") or "", input_tokens=it, output_tokens=ot)

    async def _call_groq(self, model: str, prompt: str, require_json: bool) -> RawCompletion:
        import groq
        client = self._client("groq", lambda: groq.Groq(api_key=settings.groq_api_key))

        def _call():
            return client.chat.completions.create(
                model=model, max_tokens=1000, messages=[{"role": "user", "content": prompt}]
            )
        try:
            resp = await asyncio.to_thread(_call)  # groq sync client off the event loop
        except groq.RateLimitError as e:
            raise RateLimitError(str(e)) from e
        except Exception as e:
            raise ProviderDownError(str(e)) from e
        u = resp.usage
        return RawCompletion(text=resp.choices[0].message.content or "",
                             input_tokens=u.prompt_tokens, output_tokens=u.completion_tokens)


class LowConfidence(Exception):
    """Internal marker for a model whose answer fell below the confidence gate."""
    def __init__(self, model_string: str, confidence: float, threshold: float):
        super().__init__(f"{model_string} confidence {confidence:.2f} < {threshold:.2f}")
        self.model_string = model_string
        self.confidence = confidence


# Singleton shared across FastAPI workers (Section 26).
llm_router = LLMRouter()
