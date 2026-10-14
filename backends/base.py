from abc import ABC, abstractmethod
from typing import List, Optional

from network.message import ChatMessage


class BaseBackend(ABC):
    # Token usage of the most recent generate() call, for the run log
    # (backends/tracked.py). Keys: input, output, cache_read, cache_creation,
    # response_model. None when the backend does not report usage.
    last_usage: Optional[dict] = None

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    async def generate(self, system_prompt: str, messages: List[ChatMessage]) -> str:
        pass
