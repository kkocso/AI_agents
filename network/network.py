from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, List, Optional, Tuple

from agents.orchestrator import OrchestratorAgent
from agents.parser import parse_agent_file
from agents.worker_agent import WorkerAgent
from backends.tracked import TrackedBackend
from network.message import AgentValidation, ApprovalStatus
from vendor.run_logger import sha256_text

if TYPE_CHECKING:
    from ui.cli import CLI


class AgentNetwork:
    def __init__(
        self,
        default_backend: str,
        default_model: str,
        consensus: str,
        max_rounds: int,
        run=None,
    ):
        # run: optional vendor.run_logger.RunLogger. With run=None the
        # network behaves exactly as before (no tracking, no extra calls).
        self.run = run
        self.task_outcomes: List[dict] = []  # per task: rounds_used, consensus
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
            if self.run is not None:
                slot = ("orchestrator" if parsed.is_orchestrator
                        else f"worker_{len(self.workers) + 1}")
                parsed.agent.backend = TrackedBackend(
                    parsed.agent.backend, self.run, parsed.agent.role, slot)
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

    async def run_task(self, task: str, cli: CLI) -> Tuple[str, List[AgentValidation]]:
        self._worker_feedbacks = {}
        if self.run is not None:
            self.run.count("tasks")
            # hash + length only: the task text never reaches runs/
            self.run.event("task_submitted", actor="human",
                           task_sha256=sha256_text(task)[:16], chars=len(task))
        self._mark("feedback_round")

        # Phase 1: Gather feedback from all workers in parallel
        cli.phase("Gathering feedback from all agents...")
        feedbacks = await self._gather_feedback(task, cli)

        # Optional human input before synthesis
        human_note = await cli.optional_input("Add context before the orchestrator plans? (Enter to skip)")
        if human_note:
            cli.human_note(human_note)
            self._human_note_event("before_synthesis")

        # Phase 2: Orchestrator synthesizes initial plan
        self._mark("synthesis")
        cli.phase(f"{self.orchestrator.name} is synthesizing a plan...")
        with cli.thinking(self.orchestrator.name):
            plan = await self.orchestrator.synthesize_plan(task, feedbacks, human_note)
        cli.show_plan(plan, version=1)

        # Phase 3: Validation rounds
        plan, dissatisfied = await self._validation_rounds(task, plan, feedbacks, cli)
        return plan, dissatisfied

    async def _gather_feedback(self, task: str, cli: CLI) -> List[Tuple[str, str]]:
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
        cli: CLI,
    ) -> Tuple[str, List[AgentValidation]]:
        current_plan = plan

        for round_num in range(1, self.max_rounds + 1):
            self._mark(f"validation_round_{round_num}")
            cli.phase(f"Validation Round {round_num}/{self.max_rounds}")

            human_note = await cli.optional_input(
                "Add context or guidance before agents validate? (Enter to skip)"
            )
            if human_note:
                cli.human_note(human_note)
                self._human_note_event(f"validation_round_{round_num}")

            # All workers validate in parallel
            async def validate(worker: WorkerAgent) -> AgentValidation:
                my_feedback = self._worker_feedbacks.get(worker.name, "")
                return await worker.validate_plan(task, current_plan, my_feedback)

            with cli.thinking_all([w.name for w in self.workers]):
                validations = list(await asyncio.gather(*[validate(w) for w in self.workers]))

            cli.show_validations(validations)
            concerns = [v for v in validations if v.status == ApprovalStatus.CONCERN]
            self._count_round(round_num, validations, concerns)

            if self._consensus_reached(validations):
                self._record_task_end(round_num, True, 0)
                return current_plan, []

            if round_num == self.max_rounds:
                self._record_task_end(round_num, False, len(concerns))
                return current_plan, concerns

            self._mark(f"refine_round_{round_num}")
            cli.phase(f"{self.orchestrator.name} is refining the plan...")
            with cli.thinking(self.orchestrator.name):
                current_plan = await self.orchestrator.refine_plan(
                    task, current_plan, feedbacks, concerns, human_note
                )
            cli.show_plan(current_plan, version=round_num + 1)

        return current_plan, []

    # ------------------------------------------------------- run-log helpers
    # All no-ops with run=None. Only counts, hashes and stage names are
    # logged -- never task, plan, persona or chat text, nor agent names.

    def _mark(self, stage: str) -> None:
        if self.run is not None:
            self.run.mark_stage(stage)

    def _human_note_event(self, where: str) -> None:
        if self.run is not None:
            self.run.count("human_notes")
            self.run.event("human_note", actor="human", where=where)

    def _count_round(self, round_num: int, validations, concerns) -> None:
        if self.run is None:
            return
        approvals = len(validations) - len(concerns)
        self.run.count("validation_rounds")
        self.run.count(f"validation.round_{round_num}.approvals", approvals)
        self.run.count(f"validation.round_{round_num}.concerns", len(concerns))
        self.run.count("validations.approve", approvals)
        self.run.count("validations.concern", len(concerns))

    def _record_task_end(self, rounds_used: int, consensus: bool, unresolved: int) -> None:
        self.task_outcomes.append({"rounds_used": rounds_used, "consensus": consensus})
        if self.run is None:
            return
        self.run.count("rounds_used", rounds_used)
        self.run.count("consensus_reached", 1 if consensus else 0)
        self.run.count("tasks_without_consensus", 0 if consensus else 1)
        self.run.count("unresolved_concerns", unresolved)
        self.run.event("planning_finished", actor="script", consensus=consensus,
                       rounds_used=rounds_used, max_rounds=self.max_rounds,
                       unresolved_concerns=unresolved)

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
