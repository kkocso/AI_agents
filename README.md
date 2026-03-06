# AI Agent Network

A CLI tool that launches a network of LLM-powered agents to collaboratively plan a software feature. One agent acts as the **orchestrator** (e.g. a Product Owner), and the rest are **workers** (e.g. Architect, Tech Lead, Business Analyst). Agents discuss, challenge, and iterate on a plan until they reach consensus — or until the maximum number of rounds is exhausted.

Runs fully offline via [Ollama](https://ollama.com), or against the Claude API. Configurable globally and per agent.

## How it works

Every time you submit a task:

1. All workers are asked **in parallel** for their requirements, concerns, and must-haves
2. The orchestrator reads all feedback and writes an initial plan
3. All workers **validate the plan in parallel** — each responds `APPROVE` or `CONCERN` with reasoning
4. If consensus isn't reached, the orchestrator refines the plan and the cycle repeats
5. After the maximum number of rounds, the final plan is presented — along with a clear list of which agents still have unresolved concerns and exactly why
6. The session enters **free chat mode**: talk to any agent individually or broadcast to all of them

You can inject guidance at any point — before synthesis or before any validation round — and it will be woven directly into the orchestrator's next prompt.

## Setup

```bash
pip install -r requirements.txt

# If using Ollama (default):
ollama pull llama3.2

# If using Claude API:
export ANTHROPIC_API_KEY=your_key_here
```

## Running

```bash
python main.py --agents personas/orchestrator.md personas/business_analyst.md personas/architect.md personas/tech_lead.md
```

With options:

```bash
python main.py \
  --agents personas/*.md \
  --backend claude \
  --model claude-sonnet-4-6 \
  --consensus majority \
  --max-rounds 3
```

## CLI flags

| Flag | Default | Description |
|---|---|---|
| `--agents` | *(required)* | One or more persona `.md` files. Exactly one must declare `role: orchestrator`. |
| `--backend` | `ollama` | Default LLM backend: `ollama` or `claude`. Overridable per agent. |
| `--model` | `llama3.2` | Model name for the default backend. |
| `--consensus` | `all` | `all` — every worker must approve. `majority` — more than half. |
| `--max-rounds` | `5` | How many plan-then-validate cycles before forcing a conclusion. |

## Defining agents

Each agent is a plain Markdown file. A YAML frontmatter block at the top declares identity and backend settings. Everything after the closing `---` becomes the agent's **system prompt** — the persona the LLM adopts for the entire session.

```markdown
---
name: Architect
role: worker
backend: ollama
model: llama3.2
---

You are a senior Software Architect. You focus on system design,
scalability, and long-term technical health. Push back on unnecessary
complexity and insist on documented architectural decisions.
```

Frontmatter keys:

| Key | Required | Description |
|---|---|---|
| `name` | No | Display name. Falls back to the filename if omitted. |
| `role` | No | `orchestrator` or `worker` (default). Exactly one file per session must be `orchestrator`. |
| `backend` | No | `ollama` or `claude`. Overrides the global `--backend` for this agent only. |
| `model` | No | Model string. If `backend: claude` is set without a model, `claude-sonnet-4-6` is used automatically. |

Four example personas are included in `personas/`:

- `orchestrator.md` — Product Owner: synthesizes and refines the plan
- `business_analyst.md` — Business Analyst: requirements, acceptance criteria, compliance
- `architect.md` — Architect: system design, non-functional requirements, technical debt
- `tech_lead.md` — Tech Lead: implementation feasibility, testing, CI/CD

## Chat mode

After planning completes you enter free chat. Agents remember their persona throughout.

```
[You] Chat: @architect Can you expand on the API design you mentioned?
[You] Chat: @all What's the single biggest risk in this plan?
[You] Chat: How would a rollback work?      ← no @ prefix broadcasts to all agents
[You] Chat: new                             ← submit a new task
[You] Chat: quit                            ← exit
```

## Project structure

```
├── main.py                  # entry point and chat loop
├── agents/
│   ├── base_agent.py        # BaseAgent with _generate()
│   ├── orchestrator.py      # synthesize_plan, refine_plan
│   ├── worker_agent.py      # give_feedback, validate_plan
│   └── parser.py            # reads and parses .md persona files
├── backends/
│   ├── base.py              # abstract BaseBackend interface
│   ├── ollama_backend.py    # ollama.AsyncClient wrapper
│   └── claude_backend.py    # anthropic.AsyncAnthropic wrapper
├── network/
│   ├── message.py           # ChatMessage, AgentValidation, ApprovalStatus
│   └── network.py           # planning loop, consensus logic, chat routing
├── ui/
│   └── cli.py               # Rich terminal UI
└── personas/                # example agent persona files
```

For a detailed line-by-line explanation of how the code works, see [`walkthrough.md`](walkthrough.md).
