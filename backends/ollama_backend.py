from typing import List

from ollama import AsyncClient

from backends.base import BaseBackend
from network.message import ChatMessage


class OllamaBackend(BaseBackend):
    def __init__(self, model: str = "llama3.2"):
        super().__init__(model)
        self._client = AsyncClient()

    async def generate(self, system_prompt: str, messages: List[ChatMessage]) -> str:
        formatted = [{"role": "system", "content": system_prompt}]
        formatted += [{"role": m.role, "content": m.content} for m in messages]
        response = await self._client.chat(model=self.model, messages=formatted)
        return response.message.content
