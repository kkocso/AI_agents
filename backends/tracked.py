"""Run-log wrapper for any BaseBackend.

Conceptually aligned with ``vsp/llm/tracked.py`` (``TrackedLLM``) in the
offline_AI_for_Office repo, whose ``vsp/llm/base.py`` mirrors
``backends/base.py`` here: one wrapper class, same constructor idea
(inner backend + run log), same outcome vocabulary. Keep the two in step.

Every LLM call of a session goes through ``RunLogger.track_llm()`` (global
rule: no exceptions). Recorded per call: provider, model, prompt_id
(``<role>.<method>``, e.g. ``worker.validate_plan``), prompt_version, tokens
(from the backend's ``last_usage``, incl. Anthropic cache read/creation),
latency, estimated cost and the outcome:

- ``accepted``    -- a usable answer
- ``no_answer``   -- the model answered, but with nothing usable (the
                     backend's "(No response ...)" / "(Empty response ...)"
                     placeholder text)
- ``unavailable`` -- the model could not be reached (connection, timeout,
                     server overloaded / 5xx, per-minute rate limit); reason
                     ``connection`` / ``timeout`` / ``server`` /
                     ``rate_limited`` / ``unknown``
- ``error``       -- the provider refused the request and the OPERATOR must
                     fix something (Gabor's rule: "error" is reserved for
                     this); reason ``auth`` (bad / missing key, 401/403),
                     ``quota`` (credit balance / quota used up),
                     ``model_not_found`` (404, e.g. Ollama model not pulled),
                     ``bad_request`` (other 4xx). A ``AI_REFUSED`` warning
                     with the reason code lands in run_report.md.
                     In both cases the exception is re-raised unchanged.
- ``rejected``    -- a validator threw out the answer; used for plan
                     validations that are not in the ``STATUS:`` format and
                     therefore fall back to CONCERN

None of these means the AI gave a wrong answer -- see the CLAUDE.md wording
rule. Only fixed reason codes are logged, never the exception message.

prompt_version = ``<template version>+<persona hash>``: the task template
is versioned in ``prompts/``, the persona (system prompt) is user-local and
gitignored, so it is identified only by a short SHA-256 -- never its text.
Prompt and answer texts are stored as hash + length only, unless the run was
started with ``--debug-trace``.

Without a TrackedBackend nothing of this runs: agents call the inner
backend's ``generate`` exactly as before.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from backends.base import BaseBackend
from network.message import ChatMessage
from vendor.run_logger import sha256_text

# Placeholder strings the backends return instead of an answer. Behaviour is
# unchanged (the placeholder is still returned); the run log just calls it
# no_answer.
NO_ANSWER_TEXTS = frozenset({
    "(No response from the model.)",
    "(No text in the model response.)",
    "(No response from Ollama.)",
    "(Empty response from Ollama.)",
})

_PROVIDERS = {"ClaudeBackend": "anthropic", "OllamaBackend": "ollama"}


def _exception_chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def classify_exception(exc: BaseException) -> Tuple[str, str]:
    """(outcome, reason) for a failed backend call. Duck-typed (class name +
    ``status_code``) so it works for anthropic and ollama errors and for the
    RuntimeError the Ollama backend wraps them in. The message text is only
    inspected for the Anthropic "credit balance" case, never logged."""
    for e in _exception_chain(exc):
        name = type(e).__name__
        status = getattr(e, "status_code", None)
        status = status if isinstance(status, int) and status > 0 else None
        text = str(e).lower()
        if name in ("AuthenticationError", "PermissionDeniedError", "CredentialsError") \
                or status in (401, 403):
            return "error", "auth"
        if status == 402 or "credit balance" in text or "insufficient_quota" in text:
            return "error", "quota"
        if name == "NotFoundError" or status == 404:
            return "error", "model_not_found"
        if name in ("APITimeoutError", "TimeoutError", "ReadTimeout", "ConnectTimeout") \
                or isinstance(e, TimeoutError):
            return "unavailable", "timeout"
        if name == "RateLimitError" or status == 429:
            return "unavailable", "rate_limited"
        if name in ("OverloadedError", "InternalServerError", "ServiceUnavailableError") \
                or (status is not None and status >= 500):
            return "unavailable", "server"
        if status is not None and 400 <= status < 500:
            return "error", "bad_request"
        if name == "APIConnectionError" or isinstance(e, (ConnectionError, OSError)):
            return "unavailable", "connection"
    return "unavailable", "unknown"


def persona_hash(persona: str) -> str:
    return sha256_text(persona)[:8]


def estimate_cost(price: Optional[dict], usage: dict) -> Optional[float]:
    """USD estimate from a price row (per million tokens). Anthropic reports
    input_tokens WITHOUT the cached part, so the three input kinds add up."""
    if not price:
        return None
    usd = ((usage.get("input") or 0) * price.get("input", 0)
           + (usage.get("output") or 0) * price.get("output", 0)
           + (usage.get("cache_read") or 0) * price.get("cache_read", price.get("input", 0))
           + (usage.get("cache_creation") or 0) * price.get("cache_write_5m", price.get("input", 0))
           ) / 1_000_000
    return round(usd, 6)


class TrackedBackend(BaseBackend):
    def __init__(self, inner: BaseBackend, run, agent_role: str, agent_slot: str = ""):
        super().__init__(inner.model)
        self.inner = inner
        self.run = run
        self.agent_role = agent_role      # "orchestrator" | "worker"
        self.agent_slot = agent_slot      # "worker_2" -- never the persona name
        self.provider = getattr(inner, "provider", None) or _PROVIDERS.get(
            type(inner).__name__, type(inner).__name__)

    async def generate(self, system_prompt: str, messages: List[ChatMessage]) -> str:
        return await self.generate_tracked(system_prompt, messages, method="generate")

    async def generate_tracked(
        self,
        system_prompt: str,
        messages: List[ChatMessage],
        *,
        method: str,
        template_version: str = "0",
        validator: Optional[Callable[[str], bool]] = None,
    ) -> str:
        p_hash = persona_hash(system_prompt)
        with self.run.track_llm(provider=self.provider, model=self.inner.model,
                                prompt_id=f"{self.agent_role}.{method}",
                                prompt_version=f"{template_version}+{p_hash}") as call:
            call.record["prompt.template_version"] = template_version
            call.record["persona.sha256"] = p_hash
            if self.agent_slot:
                call.record["agent.slot"] = self.agent_slot
            call.text(prompt="\n\n".join(m.content for m in messages))
            self.inner.last_usage = None
            try:
                answer = await self.inner.generate(system_prompt, messages)
            except Exception as e:
                outcome, reason = classify_exception(e)
                call.outcome(outcome)
                call.record["reason"] = reason
                call.record["error.type"] = type(e).__name__
                if outcome == "error" and not any(
                        w.get("code") == "AI_REFUSED" and w.get("where") == reason
                        for w in self.run.warnings):
                    self.run.warn("AI request refused by the provider - operator action needed",
                                  code="AI_REFUSED", where=reason)
                raise
            usage = getattr(self.inner, "last_usage", None) or {}
            call.tokens(input=usage.get("input"), output=usage.get("output"),
                        cache_read=usage.get("cache_read"))
            if usage.get("cache_creation") is not None:
                call.record["gen_ai.usage.cache_creation_input_tokens"] = int(usage["cache_creation"])
            if usage.get("response_model"):
                call.response_model(usage["response_model"])
            cost = estimate_cost((getattr(self.run, "price_table", None) or {}).get(self.inner.model), usage)
            if cost is not None:
                call.cost(cost)
            call.text(response=answer)
            if answer in NO_ANSWER_TEXTS or not (answer or "").strip():
                call.outcome("no_answer")
            elif validator is not None and not validator(answer):
                call.outcome("rejected")
            else:
                call.outcome("accepted")
            return answer
