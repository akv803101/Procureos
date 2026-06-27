"""LLMRouter (PRD Section 26): routing, fallback, confidence gate, breaker."""
import json

import pytest

from agents.llm_router import (
    CircuitBreaker,
    LLMRouter,
    LLMTask,
    RawCompletion,
    safe_parse_json,
)
from core.errors import (
    AllModelsFailedError,
    LLMParseError,
    ProviderDownError,
    QuoteAmbiguousError,
    RateLimitError,
)


def make_handlers(by_model: dict):
    """Build the four provider handlers, all dispatching on model_name. Each
    value is a RawCompletion to return or an Exception to raise."""
    async def handler(model_name, prompt, require_json):
        beh = by_model.get(model_name)
        if beh is None:
            raise ProviderDownError(f"no fake configured for {model_name}")
        if isinstance(beh, Exception):
            raise beh
        return beh
    return {p: handler for p in ("anthropic", "openai", "gemini", "groq")}


# ── routing + fallback ──────────────────────────────────────────────────────
async def test_primary_model_success():
    r = LLMRouter(handlers=make_handlers(
        {"llama-3.3-70b-versatile": RawCompletion('{"category":"fb","confidence":0.9}')}))
    res = await r.complete(LLMTask.INTENT_PARSING, "x")
    assert res.provider == "groq"
    assert res.fallback_used is False
    assert res.confidence == 0.9
    assert json.loads(res.text)["category"] == "fb"


async def test_fallback_on_provider_down():
    r = LLMRouter(handlers=make_handlers({
        "llama-3.3-70b-versatile": ProviderDownError("down"),   # primary down
        "llama-3.1-8b-instant": RawCompletion('{"category":"fb","confidence":0.95}'),
    }))
    res = await r.complete(LLMTask.INTENT_PARSING, "x")
    assert res.model == "groq/llama-3.1-8b-instant"
    assert res.fallback_used is True


async def test_all_models_failed_raises():
    r = LLMRouter(handlers=make_handlers({
        "llama-3.1-8b-instant": ProviderDownError("d"),
        "llama-3.3-70b-versatile": ProviderDownError("d"),
        "gemini-1.5-flash": ProviderDownError("d"),
        "claude-haiku-4-5": ProviderDownError("d"),
    }))
    with pytest.raises(AllModelsFailedError):
        await r.complete(LLMTask.INTENT_PARSING, "x")


# ── confidence gate (Fix 13) ────────────────────────────────────────────────
async def test_confidence_gate_falls_through_to_stronger_model():
    r = LLMRouter(handlers=make_handlers({
        "claude-sonnet-4-6": RawCompletion('{"price":100,"confidence":0.5}'),
        "gpt-4o": RawCompletion('{"price":100,"confidence":0.95}'),
    }))
    res = await r.complete(LLMTask.QUOTE_PARSING, "x", min_confidence=0.85)
    assert res.provider == "openai"
    assert res.confidence == 0.95


async def test_quote_ambiguous_when_all_below_gate():
    r = LLMRouter(handlers=make_handlers({
        "claude-sonnet-4-6": RawCompletion('{"confidence":0.5}'),
        "gpt-4o": RawCompletion('{"confidence":0.6}'),
        "gemini-1.5-pro": RawCompletion('{"confidence":0.4}'),
        "llama-3.3-70b-versatile": RawCompletion('{"confidence":0.5}'),
    }))
    with pytest.raises(QuoteAmbiguousError):
        await r.complete(LLMTask.QUOTE_PARSING, "x", min_confidence=0.85)


async def test_missing_confidence_field_defaults_to_pass():
    # Option ranking output has no confidence field -> defaults to 1.0 -> passes.
    r = LLMRouter(handlers=make_handlers(
        {"llama-3.3-70b-versatile": RawCompletion('{"ranked_options":[]}')}))
    res = await r.complete(LLMTask.OPTION_RANKING, "x", min_confidence=0.0)
    assert res.confidence == 1.0


# ── JSON handling ───────────────────────────────────────────────────────────
async def test_require_json_false_skips_parse_and_gate():
    r = LLMRouter(handlers=make_handlers(
        {"llama-3.1-8b-instant": RawCompletion("plain text, not json")}))
    res = await r.complete(LLMTask.RFQ_GENERATION, "x", require_json=False)
    assert res.text == "plain text, not json"
    assert res.confidence is None


async def test_unparseable_json_falls_through():
    r = LLMRouter(handlers=make_handlers({
        "llama-3.3-70b-versatile": RawCompletion("not json at all"),  # primary unparseable
        "llama-3.1-8b-instant": RawCompletion('{"category":"fb","confidence":0.9}'),
    }))
    res = await r.complete(LLMTask.INTENT_PARSING, "x")
    assert res.model == "groq/llama-3.1-8b-instant"


async def test_json_task_returns_cleaned_canonical_json():
    # Real models often wrap JSON in ```fences``` despite the prompt. The router
    # parses internally to gate confidence, but must ALSO hand back clean JSON so a
    # caller's plain json.loads(result.text) works (regression: live Groq broke
    # parse_intent because result.text used to be the raw fenced model output).
    r = LLMRouter(handlers=make_handlers(
        {"llama-3.3-70b-versatile": RawCompletion('```json\n{"category":"fb","confidence":0.9}\n```')}))
    res = await r.complete(LLMTask.INTENT_PARSING, "x")
    assert json.loads(res.text) == {"category": "fb", "confidence": 0.9}  # no fence-stripping by caller


# ── circuit breaker (Fix 20) ────────────────────────────────────────────────
async def test_rate_limit_falls_through_without_tripping_breaker():
    r = LLMRouter(handlers=make_handlers({
        "llama-3.3-70b-versatile": RateLimitError("429"),   # primary rate-limited
        "llama-3.1-8b-instant": RawCompletion('{"category":"fb","confidence":0.9}'),
    }))
    res = await r.complete(LLMTask.INTENT_PARSING, "x")
    assert res.model == "groq/llama-3.1-8b-instant"
    assert r._breakers["groq"].is_open() is False


async def test_open_breaker_skips_provider():
    r = LLMRouter(handlers=make_handlers({
        "claude-sonnet-4-6": RawCompletion('{"confidence":0.99}'),  # would succeed
        "gpt-4o": RawCompletion('{"confidence":0.99}'),
    }))
    for _ in range(5):
        r._breakers["anthropic"].record_failure()
    assert r._breakers["anthropic"].is_open() is True
    res = await r.complete(LLMTask.QUOTE_PARSING, "x", min_confidence=0.85)
    assert res.provider == "openai"  # anthropic skipped despite being first


# ── usage logging ───────────────────────────────────────────────────────────
async def test_usage_logger_invoked_on_success():
    logged = []

    async def logger(rec):
        logged.append(rec)

    r = LLMRouter(
        handlers=make_handlers({"llama-3.1-8b-instant": RawCompletion('{"category":"fb","confidence":0.9}')}),
        usage_logger=logger,
    )
    await r.complete(LLMTask.INTENT_PARSING, "x", company_id="co-1")
    assert len(logged) == 1
    assert logged[0]["provider"] == "groq"
    assert logged[0]["success"] is True
    assert logged[0]["company_id"] == "co-1"


# ── CircuitBreaker unit ─────────────────────────────────────────────────────
def test_circuit_breaker_opens_after_threshold_and_resets():
    cb = CircuitBreaker(failure_threshold=3)
    assert cb.is_open() is False
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open() is False
    cb.record_failure()
    assert cb.is_open() is True
    cb.record_success()
    assert cb.is_open() is False


def test_circuit_breaker_half_opens_after_recovery_timeout():
    t = [1000.0]
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0, clock=lambda: t[0])
    cb.record_failure()
    assert cb.is_open() is True
    t[0] += 61
    assert cb.is_open() is False   # half-open: a trial call is allowed through


# ── safe_parse_json ─────────────────────────────────────────────────────────
def test_safe_parse_json_strips_fences():
    assert safe_parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert safe_parse_json('```\n{"a": 1}\n```') == {"a": 1}
    assert safe_parse_json('{"a": 1}') == {"a": 1}


def test_safe_parse_json_raises_on_garbage():
    with pytest.raises(LLMParseError):
        safe_parse_json("definitely not json")
