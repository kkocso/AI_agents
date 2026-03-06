from typing import List, Tuple

from agents.base_agent import BaseAgent
from network.message import AgentValidation, ChatMessage


class OrchestratorAgent(BaseAgent):

    async def synthesize_plan(
        self,
        task: str,
        feedbacks: List[Tuple[str, str]],
        human_note: str = "",
    ) -> str:
        feedback_block = "\n\n".join(f"### {name}\n{fb}" for name, fb in feedbacks)
        extra = f"\n\nAdditional guidance from the human: {human_note}" if human_note else ""
        messages = [ChatMessage(
            role="user",
            content=(
                f"Task to plan: {task}\n\n"
                f"Feedback from team members:\n\n{feedback_block}{extra}\n\n"
                "Create a comprehensive plan that addresses all requirements, "
                "concerns, and must-haves raised by the team. "
                "Structure it with clear sections: Overview, Requirements, "
                "Technical Approach, Risks & Mitigations, and Phases."
            ),
        )]
        return await self._generate(messages)

    async def refine_plan(
        self,
        task: str,
        current_plan: str,
        feedbacks: List[Tuple[str, str]],
        concerns: List[AgentValidation],
        human_note: str = "",
    ) -> str:
        concerns_block = "\n\n".join(
            f"### {v.agent_name}\n{v.reasoning}" for v in concerns
        )
        feedback_block = "\n\n".join(f"### {name}\n{fb}" for name, fb in feedbacks)
        extra = f"\n\nAdditional guidance from the human: {human_note}" if human_note else ""
        messages = [ChatMessage(
            role="user",
            content=(
                f"Task: {task}\n\n"
                f"Original team requirements:\n\n{feedback_block}\n\n"
                f"Current plan:\n{current_plan}\n\n"
                f"Unresolved concerns from team:\n\n{concerns_block}{extra}\n\n"
                "Revise the plan to specifically address these concerns "
                "without compromising requirements that are already met. "
                "Keep the same structure as before."
            ),
        )]
        return await self._generate(messages)

    async def chat(self, message: str) -> str:
        return await self._generate([ChatMessage(role="user", content=message)])
