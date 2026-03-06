# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the network

```bash
# Install dependencies
pip install -r requirements.txt

# First-time setup: copy examples to your local (gitignored) personas folder
cp -r personas.example/ personas/

# Product Owner leading a delivery team
python main.py --agents personas/orchestrators/product_owner.md \
               personas/workers/business_analyst.md \
               personas/workers/architect.md \
               personas/workers/tech_lead.md

# Architect leading a tech design session
python main.py --agents personas/orchestrators/architect_orchestrator.md \
               personas/workers/tech_lead.md

# Use Claude API as the default backend
python main.py --agents personas/orchestrators/product_owner.md \
               personas/workers/*.md \
               --backend claude --model claude-sonnet-4-6

# Use majority consensus and limit to 3 rounds
python main.py --agents personas/orchestrators/product_owner.md \
               personas/workers/*.md \
               --consensus majority --max-rounds 3
```

Ollama must be running locally (`ollama serve`) when using the default ollama backend.

**Persona folders:**
- `personas.example/` — tracked in git; reference templates, do not run directly
- `personas/` — gitignored; copy from `personas.example/` and customise freely
  - `personas/orchestrators/` — pick one per session (`role: orchestrator`)
  - `personas/workers/` — combine any number freely (`role: worker`)

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

### Key design decisions to preserve

- **`network/network.py` imports `CLI` under `TYPE_CHECKING` only** — this is intentional to avoid a runtime circular dependency (network layer importing the UI layer). Do not move it to a top-level import.
- **`_parse_validation` defaults to `CONCERN`** — if the LLM response is ambiguous or malformed, the parser in `agents/worker_agent.py` treats it as a concern rather than an approval. This is a deliberate safety choice; do not change the default to `APPROVE`.
- **Human notes are passed as `human_note` to `refine_plan`, not wrapped in `AgentValidation`** — `OrchestratorAgent.refine_plan` has a dedicated `human_note: str` parameter that formats the input differently from agent concerns. Keep them separate.

### `walkthrough.md`

`walkthrough.md` is gitignored and built with [showboat](https://github.com/nicholasgasior/showboat). Do not edit it by hand. To verify it is still in sync with the code run:

```bash
/Users/gaborkocsis/jira-rest-anonymizer/.venv/bin/uvx showboat verify walkthrough.md --workdir .
```
