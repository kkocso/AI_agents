from agents.base_agent import BaseAgent
from network.message import AgentValidation, ApprovalStatus, ChatMessage


class WorkerAgent(BaseAgent):

    async def give_feedback(self, task: str) -> str:
        messages = [ChatMessage(
            role="user",
            content=(
                f"The team is about to work on the following task:\n\n{task}\n\n"
                "From your specific perspective and area of expertise, provide:\n"
                "1. Your key requirements\n"
                "2. Potential concerns or risks\n"
                "3. Must-haves you will not compromise on\n\n"
                "Be specific and concise."
            ),
        )]
        return await self._generate(messages)

    async def validate_plan(self, task: str, plan: str, your_feedback: str) -> AgentValidation:
        messages = [ChatMessage(
            role="user",
            content=(
                f"Original task: {task}\n\n"
                f"Your earlier feedback: {your_feedback}\n\n"
                f"Proposed plan:\n{plan}\n\n"
                "Does this plan adequately address your requirements and concerns?\n\n"
                "Respond using EXACTLY this format:\n"
                "STATUS: APPROVE\n"
                "REASONING: <your reasoning>\n\n"
                "or\n\n"
                "STATUS: CONCERN\n"
                "REASONING: <specific unresolved issues>"
            ),
        )]
        response = await self._generate(messages)
        return _parse_validation(self.name, response)

    async def chat(self, message: str) -> str:
        return await self._generate([ChatMessage(role="user", content=message)])


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
