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
        try:
            response = await self._client.chat(model=self.model, messages=formatted)
        except (ConnectionError, OSError) as e:
            raise RuntimeError(
                f"Ollama request failed (is Ollama running?): {e}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Ollama request failed: {e}") from e
        if response is None or not getattr(response, "message", None):
            return "(No response from Ollama.)"
        content = getattr(response.message, "content", None)
        if content is None or (isinstance(content, str) and not content.strip()):
            return "(Empty response from Ollama.)"
        return content
