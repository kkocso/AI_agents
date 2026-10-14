"""Test doubles: a scripted backend and a silent CLI (no network, no TTY)."""

from contextlib import contextmanager
from typing import List

from backends.base import BaseBackend


class FakeBackend(BaseBackend):
    """Answers by prompt kind; records everything it was sent."""

    provider = "fake"

    def __init__(self, model="fake-model", validate_answers=None, exc=None, usage=None,
                 answer="ok answer"):
        super().__init__(model)
        self.validate_answers = list(validate_answers or [])
        self.exc = exc
        self.usage = usage if usage is not None else {"input": 100, "output": 20}
        self.answer = answer
        self.sent: List[tuple] = []

    async def generate(self, system_prompt, messages):
        self.sent.append((system_prompt, [(m.role, m.content) for m in messages]))
        if self.exc:
            raise self.exc
        self.last_usage = dict(self.usage)
        text = messages[-1].content
        if "Respond using EXACTLY this format" in text:
            return self.validate_answers.pop(0) if self.validate_answers else "STATUS: APPROVE\nREASONING: fine"
        return self.answer


class FakeCLI:
    def __init__(self, inputs=()):
        self.inputs = list(inputs)
        self.console = self
        self.printed = []

    def print(self, *a, **k):
        self.printed.append(" ".join(str(x) for x in a))

    async def get_input(self, prompt):
        return self.inputs.pop(0) if self.inputs else "quit"

    async def optional_input(self, prompt=""):
        return self.inputs.pop(0) if self.inputs else ""

    def __getattr__(self, name):  # phase, agent_message, show_plan, ...
        return lambda *a, **k: None

    @contextmanager
    def thinking(self, name):
        yield

    @contextmanager
    def thinking_all(self, names):
        yield
