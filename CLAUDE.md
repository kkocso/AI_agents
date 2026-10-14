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
| `agents/prompts.py` + `prompts/` | Versioned task prompt templates (`<role>.<method>.v<N>.md`) |
| `backends/tracked.py` | `TrackedBackend` — run-log wrapper around any backend (see Observability) |
| `observability.py` | Session run log glue: targets, Claude price table, metrics |

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

- **New backend**: subclass `backends/base.py:BaseBackend`, implement `generate(system_prompt, messages)`. Set `self.last_usage` (input/output tokens) after each call so the run log gets token counts; add the provider name to `_PROVIDERS` in `backends/tracked.py`.
- **New agent role**: subclass `agents/base_agent.py:BaseAgent`. Register it in `agents/parser.py`.
- **Different consensus logic**: modify `AgentNetwork._consensus_reached` in `network/network.py`.

### Key design decisions to preserve

- **`network/network.py` imports `CLI` under `TYPE_CHECKING` only** — this is intentional to avoid a runtime circular dependency (network layer importing the UI layer). Do not move it to a top-level import.
- **`_parse_validation` defaults to `CONCERN`** — if the LLM response is ambiguous or malformed, the parser in `agents/worker_agent.py` treats it as a concern rather than an approval. This is a deliberate safety choice; do not change the default to `APPROVE`.
- **Human notes are passed as `human_note` to `refine_plan`, not wrapped in `AgentValidation`** — `OrchestratorAgent.refine_plan` has a dedicated `human_note: str` parameter that formats the input differently from agent concerns. Keep them separate.
- **Agents never import the run log.** `BaseAgent._generate` uses `backend.generate_tracked` only if the backend is a `TrackedBackend`; with `run=None` every call is the plain `backend.generate`, byte-identical prompts (tested).

### `walkthrough.md`

`walkthrough.md` is gitignored and built with [showboat](https://github.com/nicholasgasior/showboat). Do not edit it by hand. To verify it is still in sync with the code run:

```bash
/Users/gaborkocsis/jira-rest-anonymizer/.venv/bin/uvx showboat verify walkthrough.md --workdir .
```

## Observability and evals

The global "Observability" and "Mérés (eval)" rules apply (`~/.claude/CLAUDE.md`).
Project specifics:

- **Run logger:** `vendor/run_logger.py` (version 1.2.0, vendored from
  `_shared/observability/run_logger` — do not edit here). Glue: `observability.py`.
- **One `python main.py` session = one run log** in `runs/<time>_<id>/`
  (gitignored): `run.json`, `llm_calls.jsonl`, `run_report.md` (English).
  `RUN_LOGGER_DISABLED=1` turns it off, `RUN_LOGGER_DIR` moves it.
  - Stages: `task_input`, `feedback_round`, `synthesis`, `validation_round_N`,
    `refine_round_N`, `chat_mode` (marked with `run.mark_stage()`; stage times
    include human think time at the prompts).
  - Counts: agents, rounds used vs `rounds_max`, approvals/concerns per round,
    `consensus_reached`, `tasks_without_consensus`, `unresolved_concerns`,
    `validations.malformed`, `chat_turns`.
  - Events: `task_submitted`, `human_note`, `chat_turn` (actor `human`),
    `chat_reply` (actor `llm`), `planning_finished` (actor `script`).
- **Privacy — this repo is public.** Personas and conversations are private.
  `runs/` must never contain task, plan, feedback, persona or chat text, persona
  file names or agent names: only hashes, lengths, counts, stage names and agent
  slots (`worker_2`). `--debug-trace` keeps texts for local debugging only;
  never share or commit such a `runs/`. Guarded by `tests/test_observability.py`.
- **Every LLM call goes through `backends/tracked.py:TrackedBackend`**
  (attached by `AgentNetwork(run=...)`; `run=None` = old behaviour). It mirrors
  `vsp/llm/tracked.py:TrackedLLM` in offline_AI_for_Office (whose
  `vsp/llm/base.py` mirrors `backends/base.py`) — keep the two aligned.
  - `prompt_id` = `<role>.<method>` (`worker.validate_plan`, `orchestrator.chat`, ...)
  - `prompt_version` = `<template version>+<persona sha256[:8]>`. Templates live in
    `prompts/<prompt_id>.v<N>.md` (chat has no template: version `0`); the persona
    is identified only by its hash.
  - tokens: Anthropic `usage` incl. `cache_read_input_tokens` and
    `cache_creation_input_tokens`; Ollama `prompt_eval_count` / `eval_count`.
  - outcome: `accepted`; `no_answer` (empty / "(No response …)" placeholder);
    `unavailable` (model not reachable — exception re-raised unchanged);
    `rejected` (validation answer without a STATUS verdict, which
    `_parse_validation` turns into CONCERN — the default itself is unchanged).
  - **Wording rule:** never "AI error" in anything user-facing.
- **LLM touchpoints and evals:**

  | Point | prompt_id | Eval | Last result |
  |---|---|---|---|
  | `_parse_validation` (decides consensus) | `worker.validate_plan` | `evals/validation_parse/` (deterministic, synthetic) | 2026-10-14: accuracy 0.765 on 34 cases, 4 false approvals (negated STATUS), 4 false concerns (markdown STATUS) |
  | feedback, synthesis, refine, chat | `worker.give_feedback`, `orchestrator.*`, `*.chat` | none yet (free text; judged by `rounds_to_consensus`) | — |

- **Targets** (`observability.py:TARGETS`; `label` is the name in `run_report.md`):

  | Metric | Target | Meaning |
  |---|---|---|
  | `duration_s` | ≤ 1800 s | session duration, incl. human think time |
  | `rounds_to_consensus` | ≤ 3 | mean validation rounds per task that reached consensus (no-consensus tasks show in `tasks_without_consensus`) |
  | `malformed_validation_share` | ≤ 0.05 | validation answers without a STATUS verdict (counted as CONCERN) — prompt/model format health |
  | `ai_unavailable_share` | ≤ 0.02 | AI requests where the model was not reachable |
  | `ai_no_answer_share` | ≤ 0.05 | AI requests that returned no usable answer |
  | `cost_usd_estimate` | ≤ 0.50 USD | estimated Claude cost per session (`observability.py:PRICE_TABLE` — verify prices and model ids before quoting) |

- Tests: `pip install pytest && python -m pytest -q` (no network, no Ollama, no API key).

## Autonomy matrix

| Claude on its own | Needs Gábor's approval |
|---|---|
| code + tests on a feature branch, refactors that keep prompts byte-identical, run-log / metric / eval additions, docs, adding synthetic eval cases, local runs with example personas | consensus logic (`_consensus_reached`, round flow), `_parse_validation` behaviour or its CONCERN default, prompt text or model defaults (with eval result), backend API behaviour (e.g. prompt caching, `max_tokens`), anything public-facing (README, persona examples, repo visibility, published reports), new dependencies, merging |
