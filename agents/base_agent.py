from typing import Callable, List, Optional

from backends.base import BaseBackend
from network.message import ChatMessage


class BaseAgent:
    role = "agent"

    def __init__(self, name: str, persona: str, backend: BaseBackend):
        self.name = name
        self.persona = persona
        self.backend = backend

    async def _generate(
        self,
        messages: List[ChatMessage],
        method: str = "chat",
        template_version: str = "0",
        validator: Optional[Callable[[str], bool]] = None,
    ) -> str:
        # With a run log attached (backends/tracked.py) the call is recorded
        # under prompt_id "<role>.<method>"; otherwise this is the plain
        # backend call, unchanged.
        tracked = getattr(self.backend, "generate_tracked", None)
        if tracked is not None:
            return await tracked(self.persona, messages, method=method,
                                 template_version=template_version, validator=validator)
        return await self.backend.generate(self.persona, messages)
