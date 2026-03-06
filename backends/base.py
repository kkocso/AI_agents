from abc import ABC, abstractmethod
from typing import List

from network.message import ChatMessage


class BaseBackend(ABC):
    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    async def generate(self, system_prompt: str, messages: List[ChatMessage]) -> str:
        pass
