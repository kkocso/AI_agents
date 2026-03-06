# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the network

```bash
# Install dependencies
pip install -r requirements.txt

# Basic run with the bundled example personas
python main.py --agents personas/orchestrator.md personas/business_analyst.md personas/architect.md personas/tech_lead.md

# Use Claude API as the default backend
python main.py --agents personas/*.md --backend claude --model claude-sonnet-4-6

# Use majority consensus and limit to 3 rounds
python main.py --agents personas/*.md --consensus majority --max-rounds 3
```

Ollama must be running locally (`ollama serve`) when using the default ollama backend.

## Architecture

The system runs a structured multi-agent planning loop driven by a single **orchestrator** agent and any number of **worker** agents. Each agent is defined by a Markdown file with YAML frontmatter.

### Conversation flow

1. Human submits a task.
2. All workers respond in parallel with their requirements and concerns (`give_feedback`).
3. Human may optionally add context before synthesis.
4. The orchestrator synthesizes an initial plan (`synthesize_plan`).
5. All workers validate the plan in parallel (`validate_plan`) — responding with `STATUS: APPROVE` or `STATUS: CONCERN`.
6. If consensus is not reached, the orchestrator refines the plan (`refine_plan`) and the loop repeats (up to `--max-rounds`).
7. After max rounds, agents who are still unsatisfied are listed with their unresolved concerns.
8. The session enters free **chat mode** where the human can address any agent directly.

### Key modules

| Path | Responsibility |
|---|---|
| `main.py` | Entry point, argument parsing, top-level async loop, chat mode |
| `network/network.py` | `AgentNetwork` — owns the planning loop, consensus logic, and agent registry |
| `network/message.py` | Data structures: `ChatMessage`, `AgentValidation`, `ApprovalStatus` |
| `agents/orchestrator.py` | `OrchestratorAgent` — `synthesize_plan`, `refine_plan`, `chat` |
| `agents/worker_agent.py` | `WorkerAgent` — `give_feedback`, `validate_plan`, `chat`; includes `_parse_validation` |
| `agents/parser.py` | Parses `.md` persona files (YAML frontmatter + body as system prompt) |
| `backends/ollama_backend.py` | Async Ollama client wrapper |
| `backends/claude_backend.py` | Async Anthropic client wrapper |
| `ui/cli.py` | All Rich-based terminal output and user input (`get_input`, `optional_input`) |

### Agent persona file format

```markdown
---
name: My Agent
role: worker          # or: orchestrator (exactly one file must use this)
backend: ollama       # optional, overrides --backend
model: llama3.2       # optional, overrides --model
---

Everything below the closing --- is used verbatim as the agent's system prompt.
```

If `backend: claude` is set without a `model:` key, `claude-sonnet-4-6` is used automatically.

### Extending the system

- **New backend**: subclass `backends/base.py:BaseBackend`, implement `generate(system_prompt, messages)`.
- **New agent role**: subclass `agents/base_agent.py:BaseAgent`. Register it in `agents/parser.py`.
- **Different consensus logic**: modify `AgentNetwork._consensus_reached` in `network/network.py`.
