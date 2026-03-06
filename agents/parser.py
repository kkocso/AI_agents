import re
from dataclasses import dataclass
from pathlib import Path

from agents.base_agent import BaseAgent
from agents.orchestrator import OrchestratorAgent
from agents.worker_agent import WorkerAgent
from backends.base import BaseBackend
from backends.claude_backend import DEFAULT_CLAUDE_MODEL, ClaudeBackend
from backends.ollama_backend import OllamaBackend


@dataclass
class ParsedAgent:
    name: str
    is_orchestrator: bool
    agent: BaseAgent


def parse_agent_file(
    file_path: str,
    default_backend: str,
    default_model: str,
) -> ParsedAgent:
    content = Path(file_path).read_text()

    meta: dict = {}
    persona = content

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip()
        persona = match.group(2).strip()

    name = meta.get("name", Path(file_path).stem.replace("_", " ").title())
    role = meta.get("role", "worker").lower()

    backend_explicit = "backend" in meta
    model_explicit = "model" in meta
    backend_type = meta.get("backend", default_backend).lower()
    model = meta.get("model", default_model)

    # If backend was explicitly set to claude but no model was given, use claude default
    if backend_explicit and backend_type == "claude" and not model_explicit:
        model = DEFAULT_CLAUDE_MODEL

    backend = _create_backend(backend_type, model)
    is_orchestrator = role == "orchestrator"
    agent = OrchestratorAgent(name, persona, backend) if is_orchestrator else WorkerAgent(name, persona, backend)

    return ParsedAgent(name=name, is_orchestrator=is_orchestrator, agent=agent)


def _create_backend(backend_type: str, model: str) -> BaseBackend:
    if backend_type == "claude":
        return ClaudeBackend(model=model)
    return OllamaBackend(model=model)
