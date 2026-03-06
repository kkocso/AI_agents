from typing import List

from backends.base import BaseBackend
from network.message import ChatMessage


class BaseAgent:
    def __init__(self, name: str, persona: str, backend: BaseBackend):
        self.name = name
        self.persona = persona
        self.backend = backend

    async def _generate(self, messages: List[ChatMessage]) -> str:
        return await self.backend.generate(self.persona, messages)
