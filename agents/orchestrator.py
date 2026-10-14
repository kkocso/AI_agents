from typing import List, Tuple

from agents.base_agent import BaseAgent
from agents.prompts import CHAT_TEMPLATE_VERSION, PROMPT_VERSIONS, render
from network.message import AgentValidation, ChatMessage


class OrchestratorAgent(BaseAgent):
    role = "orchestrator"

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
            content=render("orchestrator.synthesize_plan", task=task,
                           feedback_block=feedback_block, extra=extra),
        )]
        return await self._generate(messages, "synthesize_plan",
                                    PROMPT_VERSIONS["orchestrator.synthesize_plan"])

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
            content=render("orchestrator.refine_plan", task=task, feedback_block=feedback_block,
                           current_plan=current_plan, concerns_block=concerns_block, extra=extra),
        )]
        return await self._generate(messages, "refine_plan",
                                    PROMPT_VERSIONS["orchestrator.refine_plan"])

    async def chat(self, message: str) -> str:
        return await self._generate([ChatMessage(role="user", content=message)],
                                    "chat", CHAT_TEMPLATE_VERSION)
