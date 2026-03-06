import asyncio
from typing import List, Optional, Tuple

from agents.orchestrator import OrchestratorAgent
from agents.parser import parse_agent_file
from agents.worker_agent import WorkerAgent
from network.message import AgentValidation, ApprovalStatus


class AgentNetwork:
    def __init__(
        self,
        default_backend: str,
        default_model: str,
        consensus: str,
        max_rounds: int,
    ):
        self.default_backend = default_backend
        self.default_model = default_model
        self.consensus = consensus
        self.max_rounds = max_rounds
        self.orchestrator: Optional[OrchestratorAgent] = None
        self.workers: List[WorkerAgent] = []
        self._worker_feedbacks: dict[str, str] = {}

    def load_agents(self, agent_files: List[str]) -> None:
        for file_path in agent_files:
            parsed = parse_agent_file(file_path, self.default_backend, self.default_model)
            if parsed.is_orchestrator:
                if self.orchestrator is not None:
                    raise ValueError("Only one orchestrator is allowed.")
                self.orchestrator = parsed.agent
            else:
                self.workers.append(parsed.agent)

        if self.orchestrator is None:
            raise ValueError(
                "No orchestrator defined. Add 'role: orchestrator' to one agent's frontmatter."
            )
        if not self.workers:
            raise ValueError("At least one non-orchestrator agent is required.")

    async def run_task(self, task: str, cli) -> Tuple[str, List[AgentValidation]]:
        self._worker_feedbacks = {}

        # Phase 1: Gather feedback from all workers in parallel
        cli.phase("Gathering feedback from all agents...")
        feedbacks = await self._gather_feedback(task, cli)

        # Optional human input before synthesis
        human_note = await cli.optional_input("Add context before the orchestrator plans? (Enter to skip)")
        if human_note:
            cli.human_note(human_note)

        # Phase 2: Orchestrator synthesizes initial plan
        cli.phase(f"{self.orchestrator.name} is synthesizing a plan...")
        with cli.thinking(self.orchestrator.name):
            plan = await self.orchestrator.synthesize_plan(task, feedbacks, human_note)
        cli.show_plan(plan, version=1)

        # Phase 3: Validation rounds
        plan, dissatisfied = await self._validation_rounds(task, plan, feedbacks, cli)
        return plan, dissatisfied

    async def _gather_feedback(self, task: str, cli) -> List[Tuple[str, str]]:
        async def get_feedback(worker: WorkerAgent) -> Tuple[str, str]:
            return worker.name, await worker.give_feedback(task)

        with cli.thinking_all([w.name for w in self.workers]):
            results = await asyncio.gather(*[get_feedback(w) for w in self.workers])

        feedbacks = list(results)
        self._worker_feedbacks = dict(feedbacks)
        for name, feedback in feedbacks:
            cli.agent_message(name, feedback)
        return feedbacks

    async def _validation_rounds(
        self,
        task: str,
        plan: str,
        feedbacks: List[Tuple[str, str]],
        cli,
    ) -> Tuple[str, List[AgentValidation]]:
        current_plan = plan

        for round_num in range(1, self.max_rounds + 1):
            cli.phase(f"Validation Round {round_num}/{self.max_rounds}")

            human_note = await cli.optional_input(
                "Add context or guidance before agents validate? (Enter to skip)"
            )
            if human_note:
                cli.human_note(human_note)

            # All workers validate in parallel
            async def validate(worker: WorkerAgent) -> AgentValidation:
                my_feedback = self._worker_feedbacks.get(worker.name, "")
                return await worker.validate_plan(task, current_plan, my_feedback)

            with cli.thinking_all([w.name for w in self.workers]):
                validations = list(await asyncio.gather(*[validate(w) for w in self.workers]))

            cli.show_validations(validations)
            concerns = [v for v in validations if v.status == ApprovalStatus.CONCERN]

            if self._consensus_reached(validations):
                return current_plan, []

            if round_num == self.max_rounds:
                return current_plan, concerns

            # Add human note as a concern for the orchestrator if provided
            refine_concerns = list(concerns)
            if human_note:
                from network.message import AgentValidation, ApprovalStatus
                refine_concerns.append(
                    AgentValidation(
                        agent_name="Human",
                        status=ApprovalStatus.CONCERN,
                        reasoning=human_note,
                    )
                )

            cli.phase(f"{self.orchestrator.name} is refining the plan...")
            with cli.thinking(self.orchestrator.name):
                current_plan = await self.orchestrator.refine_plan(
                    task, current_plan, feedbacks, refine_concerns
                )
            cli.show_plan(current_plan, version=round_num + 1)

        return current_plan, []

    def _consensus_reached(self, validations: List[AgentValidation]) -> bool:
        approvals = sum(1 for v in validations if v.status == ApprovalStatus.APPROVE)
        if self.consensus == "all":
            return approvals == len(validations)
        return approvals > len(validations) / 2

    async def chat_with_agent(self, agent_name_lower: str, message: str) -> Optional[Tuple[str, str]]:
        """Returns (actual_name, response) or None if agent not found."""
        all_agents = [self.orchestrator] + self.workers
        for agent in all_agents:
            if agent.name.lower() == agent_name_lower:
                response = await agent.chat(message)
                return agent.name, response
        return None

    async def broadcast_chat(self, message: str) -> List[Tuple[str, str]]:
        all_agents = [self.orchestrator] + self.workers

        async def respond(agent):
            return agent.name, await agent.chat(message)

        return list(await asyncio.gather(*[respond(a) for a in all_agents]))

    @property
    def all_agent_names(self) -> List[str]:
        return [self.orchestrator.name] + [w.name for w in self.workers]
