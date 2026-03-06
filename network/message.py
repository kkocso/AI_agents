from dataclasses import dataclass
from enum import Enum


class ApprovalStatus(str, Enum):
    APPROVE = "approve"
    CONCERN = "concern"


@dataclass
class AgentValidation:
    agent_name: str
    status: ApprovalStatus
    reasoning: str


@dataclass
class ChatMessage:
    role: str  # "system", "user", "assistant"
    content: str
