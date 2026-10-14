"""Prompts moved from f-strings to prompts/<id>.v<N>.md must render the
exact same text as the original code (golden copies below, from main @ f5b9a4f)."""

import pytest

from agents.prompts import PROMPT_VERSIONS, PROMPTS_DIR, render
from network.message import AgentValidation, ApprovalStatus


# ---- golden copies of the original inline prompts -------------------------
def orig_give_feedback(task):
    return (
        f"The team is about to work on the following task:\n\n{task}\n\n"
        "From your specific perspective and area of expertise, provide:\n"
        "1. Your key requirements\n"
        "2. Potential concerns or risks\n"
        "3. Must-haves you will not compromise on\n\n"
        "Be specific and concise."
    )


def orig_validate(task, plan, your_feedback):
    return (
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
    )


def orig_synthesize(task, feedbacks, human_note=""):
    feedback_block = "\n\n".join(f"### {name}\n{fb}" for name, fb in feedbacks)
    extra = f"\n\nAdditional guidance from the human: {human_note}" if human_note else ""
    return (
        f"Task to plan: {task}\n\n"
        f"Feedback from team members:\n\n{feedback_block}{extra}\n\n"
        "Create a comprehensive plan that addresses all requirements, "
        "concerns, and must-haves raised by the team. "
        "Structure it with clear sections: Overview, Requirements, "
        "Technical Approach, Risks & Mitigations, and Phases."
    )


def orig_refine(task, current_plan, feedbacks, concerns, human_note=""):
    concerns_block = "\n\n".join(f"### {v.agent_name}\n{v.reasoning}" for v in concerns)
    feedback_block = "\n\n".join(f"### {name}\n{fb}" for name, fb in feedbacks)
    extra = f"\n\nAdditional guidance from the human: {human_note}" if human_note else ""
    return (
        f"Task: {task}\n\n"
        f"Original team requirements:\n\n{feedback_block}\n\n"
        f"Current plan:\n{current_plan}\n\n"
        f"Unresolved concerns from team:\n\n{concerns_block}{extra}\n\n"
        "Revise the plan to specifically address these concerns "
        "without compromising requirements that are already met. "
        "Keep the same structure as before."
    )


TASKS = ["Build a login page", "Task with {braces} and {task} and %s", "", "multi\nline\n\ntask"]


def test_prompt_files_exist_for_every_version():
    for pid, ver in PROMPT_VERSIONS.items():
        assert (PROMPTS_DIR / f"{pid}.v{ver}.md").is_file()


@pytest.mark.parametrize("task", TASKS)
def test_worker_prompts_identical(task):
    assert render("worker.give_feedback", task=task) == orig_give_feedback(task)
    assert render("worker.validate_plan", task=task, plan="P {x}", your_feedback="F") == \
        orig_validate(task, "P {x}", "F")


@pytest.mark.parametrize("task", TASKS)
@pytest.mark.parametrize("note", ["", "be quick {now}"])
def test_orchestrator_prompts_identical(task, note):
    from agents.orchestrator import OrchestratorAgent  # noqa: F401  (import check)
    fbs = [("A", "needs X"), ("B", "needs {Y}")]
    concerns = [AgentValidation("A", ApprovalStatus.CONCERN, "missing X")]
    feedback_block = "\n\n".join(f"### {n}\n{f}" for n, f in fbs)
    extra = f"\n\nAdditional guidance from the human: {note}" if note else ""
    assert render("orchestrator.synthesize_plan", task=task, feedback_block=feedback_block,
                  extra=extra) == orig_synthesize(task, fbs, note)
    concerns_block = "\n\n".join(f"### {v.agent_name}\n{v.reasoning}" for v in concerns)
    assert render("orchestrator.refine_plan", task=task, feedback_block=feedback_block,
                  current_plan="plan v1", concerns_block=concerns_block, extra=extra) == \
        orig_refine(task, "plan v1", fbs, concerns, note)
