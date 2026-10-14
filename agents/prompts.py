"""Versioned prompt templates (``prompts/<prompt_id>.v<N>.md``).

The task-level instructions the agents send live in files, not in code, so
every LLM call can be logged with ``prompt_id`` + ``prompt_version`` and a
prompt change shows up as a new file in review. Templates use ``str.format``
placeholders (``{task}``, ``{plan}``, ...); the inserted values are never
parsed, so braces in a task or plan are safe.

The persona (system prompt) is NOT here: persona files are user-local and
gitignored. The run log identifies a persona only by its hash.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

# prompt_id -> template version currently in use. Bump together with a new
# prompts/<id>.v<N>.md file (and an eval run, see CLAUDE.md).
PROMPT_VERSIONS = {
    "worker.give_feedback": "1",
    "worker.validate_plan": "1",
    "orchestrator.synthesize_plan": "1",
    "orchestrator.refine_plan": "1",
}

# Free chat sends the human's message as-is: there is no template, so the
# template version is "0" (the persona hash still versions the call).
CHAT_TEMPLATE_VERSION = "0"


def load_template(prompt_id: str, version: str | None = None) -> str:
    version = version or PROMPT_VERSIONS[prompt_id]
    text = (PROMPTS_DIR / f"{prompt_id}.v{version}.md").read_text(encoding="utf-8")
    # files end with exactly one newline that is not part of the prompt
    return text[:-1] if text.endswith("\n") else text


TEMPLATES = {pid: load_template(pid) for pid in PROMPT_VERSIONS}


def render(prompt_id: str, **values: str) -> str:
    return TEMPLATES[prompt_id].format(**values)
