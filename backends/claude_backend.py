import os
from typing import List

import anthropic

from backends.base import BaseBackend
from network.message import ChatMessage

DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"

# Opt-in prompt caching of the persona system prompt (PROPOSAL, off by
# default -- see CLAUDE.md "Prompt caching"). Below the model's minimum
# cacheable length (1024 tokens for Sonnet 4.6) the API silently does not
# cache; the run log's cache_read / cache_creation tokens show the effect.
PROMPT_CACHE_ENV = "CLAUDE_PROMPT_CACHE"


def _prompt_cache_enabled() -> bool:
    return os.environ.get(PROMPT_CACHE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


class ClaudeBackend(BaseBackend):
    def __init__(self, model: str = DEFAULT_CLAUDE_MODEL):
        super().__init__(model)
        self._client = anthropic.AsyncAnthropic()

    async def generate(self, system_prompt: str, messages: List[ChatMessage]) -> str:
        formatted = [{"role": m.role, "content": m.content} for m in messages]
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=_system(system_prompt),
            messages=formatted,
        )
        self.last_usage = _usage(response)
        if not response.content:
            return "(No response from the model.)"
        text_parts = [
            block.text for block in response.content
            if hasattr(block, "text") and block.text
        ]
        if not text_parts:
            return "(No text in the model response.)"
        return "".join(text_parts)


def _usage(response) -> dict:
    """Anthropic usage fields (input_tokens excludes the cached part)."""
    u = getattr(response, "usage", None)
    return {
        "input": getattr(u, "input_tokens", None),
        "output": getattr(u, "output_tokens", None),
        "cache_read": getattr(u, "cache_read_input_tokens", None),
        "cache_creation": getattr(u, "cache_creation_input_tokens", None),
        "response_model": getattr(response, "model", None),
    }


def _system(system_prompt: str):
    if not _prompt_cache_enabled():
        return system_prompt  # unchanged default request
    return [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
