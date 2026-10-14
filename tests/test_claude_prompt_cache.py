"""Opt-in persona prompt caching: default request unchanged."""

import asyncio
from types import SimpleNamespace as NS

import pytest

import backends.claude_backend as cb
from network.message import ChatMessage


class FakeMessages:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return NS(model="claude-sonnet-4-6", content=[NS(text="hi")],
                  usage=NS(input_tokens=5, output_tokens=1, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0))


@pytest.fixture
def backend(monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(cb.anthropic, "AsyncAnthropic", lambda: NS(messages=fake))
    return cb.ClaudeBackend(), fake


def test_default_sends_plain_system_string(backend, monkeypatch):
    monkeypatch.delenv(cb.PROMPT_CACHE_ENV, raising=False)
    b, fake = backend
    assert asyncio.run(b.generate("PERSONA", [ChatMessage("user", "x")])) == "hi"
    assert fake.kwargs["system"] == "PERSONA"
    assert b.last_usage["input"] == 5


def test_opt_in_marks_persona_cacheable(backend, monkeypatch):
    monkeypatch.setenv(cb.PROMPT_CACHE_ENV, "1")
    b, fake = backend
    asyncio.run(b.generate("PERSONA", [ChatMessage("user", "x")]))
    assert fake.kwargs["system"] == [{"type": "text", "text": "PERSONA",
                                      "cache_control": {"type": "ephemeral"}}]
