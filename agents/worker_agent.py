from agents.base_agent import BaseAgent
from agents.prompts import CHAT_TEMPLATE_VERSION, PROMPT_VERSIONS, render
from network.message import AgentValidation, ApprovalStatus, ChatMessage


class WorkerAgent(BaseAgent):
    role = "worker"

    async def give_feedback(self, task: str) -> str:
        messages = [ChatMessage(
            role="user",
            content=render("worker.give_feedback", task=task),
        )]
        return await self._generate(messages, "give_feedback",
                                    PROMPT_VERSIONS["worker.give_feedback"])

    async def validate_plan(self, task: str, plan: str, your_feedback: str) -> AgentValidation:
        messages = [ChatMessage(
            role="user",
            content=render("worker.validate_plan", task=task,
                           your_feedback=your_feedback, plan=plan),
        )]
        # validator only labels the run-log outcome ("rejected" when the
        # answer is not in STATUS format); the decision is _parse_validation's
        response = await self._generate(messages, "validate_plan",
                                        PROMPT_VERSIONS["worker.validate_plan"],
                                        validator=validation_is_well_formed)
        return _parse_validation(self.name, response)

    async def chat(self, message: str) -> str:
        return await self._generate([ChatMessage(role="user", content=message)],
                                    "chat", CHAT_TEMPLATE_VERSION)


def _parse_validation(agent_name: str, response: str) -> AgentValidation:
    status = ApprovalStatus.CONCERN  # default to concern (safer)

    for line in response.splitlines():
        if line.strip().upper().startswith("STATUS:"):
            if "APPROVE" in line.upper():
                status = ApprovalStatus.APPROVE
            break

    reasoning = response
    idx = response.lower().find("reasoning:")
    if idx != -1:
        reasoning = response[idx + len("reasoning:"):].strip()

    return AgentValidation(agent_name=agent_name, status=status, reasoning=reasoning)


def validation_is_well_formed(response: str) -> bool:
    """True when _parse_validation found an explicit verdict: the first
    ``STATUS:`` line says APPROVE or CONCERN. False means the CONCERN came
    from the safe default (no STATUS line, or a STATUS line with neither
    word) -- the run log records that answer as ``rejected``.

    Observability only: it mirrors _parse_validation's line scan and never
    changes the parsed status.
    """
    for line in response.splitlines():
        if line.strip().upper().startswith("STATUS:"):
            up = line.upper()
            return "APPROVE" in up or "CONCERN" in up
    return False
