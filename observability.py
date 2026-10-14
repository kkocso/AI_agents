"""Run-log glue for `python main.py` sessions (see vendor/run_logger.py and
the "Observability" section of CLAUDE.md).

One interactive session = one run: runs/<time>_<id>/ (gitignored) with
run.json, llm_calls.jsonl and run_report.md (English).

What goes in: stage timings (feedback_round, synthesis, validation_round_N,
refine_round_N, chat_mode, task_input), counts (agents, rounds used vs max,
approvals/concerns per round, consensus, unresolved concerns), events with
actor human/llm/script (that a task, a human note or a chat turn happened --
never their text), every LLM call (backends/tracked.py) and the targets below.

What never goes in (unless --debug-trace, locally): task, plan, persona,
feedback or chat text, persona file names, agent names. Those are private;
this repo is public.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Iterator, Optional

from vendor.run_logger import RunLogger

TOOL = "ai-agents"
ROOT = Path(__file__).resolve().parent

# --- Targets: the one place to tune them ----------------------------------
# direction "max": value <= target is good; "min": value >= target is good.
# `label` is the human-readable name shown in run_report.md.
TARGETS = {
    "duration_s": {"target": 1800, "direction": "max", "unit": " s",
                   "label": "Session duration (includes human think time)"},
    "rounds_to_consensus": {"target": 3, "direction": "max",
                            "label": "Validation rounds needed to reach consensus (mean per task)"},
    "malformed_validation_share": {"target": 0.05, "direction": "max",
                                   "label": "Plan validations not in STATUS format (counted as CONCERN)"},
    "ai_unavailable_share": {"target": 0.02, "direction": "max",
                             "label": "AI requests where the model was not reachable"},
    "ai_no_answer_share": {"target": 0.05, "direction": "max",
                           "label": "AI requests that returned no usable answer"},
    "ai_error_share": {"target": 0.0, "direction": "max",
                       "label": "AI requests refused by the provider (key, quota or model name - operator must fix)"},
    "cost_usd_estimate": {"target": 0.50, "direction": "max", "unit": " USD",
                          "label": "Estimated Claude API cost of the session"},
}

# --- Claude price table (USD per million tokens) --------------------------
# Prices: checked against https://platform.claude.com/docs/en/about-claude/pricing
# on 2026-10-14 (two separate reads, all values identical), incl. cache reads.
# Note the page's exceptions to the usual 0.1x cache-read multiplier:
# Opus 5.5 and Sonnet 5.5 use 0.05x (hence $0.20 / $0.10 below).
# STILL TO VERIFY: the model ids (keys). The pricing page shows model names,
# not API ids; the keys below are assumed from the docs' slugs and must equal
# the id passed to the API (--model / persona `model:`), else cost stays empty.
# Ollama runs locally and has no price (cost stays empty).
PRICE_TABLE = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write_5m": 3.75},
    "claude-opus-4-6":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write_5m": 6.25},
    "claude-opus-4-7":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write_5m": 6.25},
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00,  "cache_read": 0.10, "cache_write_5m": 1.25},
    "claude-sonnet-5":   {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write_5m": 2.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.10, "cache_write_5m": 2.50},
    "claude-opus-5":     {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write_5m": 6.25},
    "claude-opus-5-5":   {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write_5m": 5.00},
}


INTERRUPTS = (KeyboardInterrupt, asyncio.CancelledError, EOFError)


@contextlib.contextmanager
def session_run(run: RunLogger) -> Iterator[RunLogger]:
    """Like ``with run:``, but a user ending the session with Ctrl-C / Ctrl-D
    is recorded as status ``interrupted`` (not ``failed``) plus a
    ``session_interrupted`` event (actor human). The exception is re-raised."""
    try:
        yield run
    except INTERRUPTS as e:
        run._end_marked("interrupted")
        run.status = "interrupted"
        run.error = {"type": type(e).__name__}
        run.event("session_interrupted", actor="human")
        run.close()
        raise
    except BaseException as e:
        run.__exit__(type(e), e, e.__traceback__)
        raise
    else:
        run.__exit__(None, None, None)


def start_run(args, runs_dir: Optional[Path] = None) -> RunLogger:
    """Session run log. The config hash covers only non-private settings
    (backend, model, consensus rule, max rounds) -- not persona paths."""
    cfg = {"backend": args.backend, "model": args.model,
           "consensus": args.consensus, "max_rounds": args.max_rounds}
    return RunLogger(TOOL, runs_dir=runs_dir or ROOT / "runs", config=cfg,
                     actor="human", debug_trace=bool(getattr(args, "debug_trace", False)) or None,
                     lang="en", price_table=PRICE_TABLE, project_root=ROOT)


def record_session_start(run: RunLogger, network, args) -> None:
    run.count("agents.workers", len(network.workers))
    run.count("agents.orchestrator", 1 if network.orchestrator is not None else 0)
    run.count("rounds_max", args.max_rounds)
    providers = {}
    for agent in [network.orchestrator, *network.workers]:
        if agent is None:
            continue
        p = getattr(agent.backend, "provider", "?")
        providers[p] = providers.get(p, 0) + 1
    for p, n in providers.items():
        run.count(f"agents.backend.{p}", n)


def record_chat_turn(run: Optional[RunLogger], target: str, replies: int = 0) -> None:
    """A human chat message and the agents' replies happened.
    target: 'all' | 'agent' | 'unknown_agent' -- never the text or agent name."""
    if run is None:
        return
    run.count("chat_turns")
    run.event("chat_turn", actor="human", target=target)
    if replies:
        run.count("chat_replies", replies)
        run.event("chat_reply", actor="llm", replies=replies)


def _metric(run: RunLogger, name: str, value) -> None:
    spec = TARGETS.get(name, {})
    run.metric(name, value, target=spec.get("target"), direction=spec.get("direction", "max"),
               unit=spec.get("unit", ""), label=spec.get("label"))


def record_metrics(run: RunLogger, network=None) -> None:
    """Session metrics; call once at the end (also after a crash)."""
    _metric(run, "duration_s", round(time.perf_counter() - run._t0, 1))

    outcomes = [t for t in (getattr(network, "task_outcomes", None) or []) if t["consensus"]]
    if outcomes:
        _metric(run, "rounds_to_consensus",
                round(sum(t["rounds_used"] for t in outcomes) / len(outcomes), 2))

    calls = run.llm_calls
    validations = [c for c in calls if c.get("prompt.id") == "worker.validate_plan"
                   and c.get("outcome") in ("accepted", "rejected", "no_answer")]
    if validations:
        malformed = sum(1 for c in validations if c["outcome"] != "accepted")
        run.count("validations.malformed", malformed)
        _metric(run, "malformed_validation_share", round(malformed / len(validations), 3))

    if calls:
        n = len(calls)
        _metric(run, "ai_unavailable_share",
                round(sum(1 for c in calls if c.get("outcome") == "unavailable") / n, 3))
        _metric(run, "ai_no_answer_share",
                round(sum(1 for c in calls if c.get("outcome") == "no_answer") / n, 3))
        _metric(run, "ai_error_share",
                round(sum(1 for c in calls if c.get("outcome") == "error") / n, 3))
        cache_creation = sum(c.get("gen_ai.usage.cache_creation_input_tokens") or 0 for c in calls)
        if cache_creation:
            run.count("cache_creation_input_tokens", cache_creation)

    costs = [c["cost_usd_estimate"] for c in calls if c.get("cost_usd_estimate") is not None]
    if costs:
        _metric(run, "cost_usd_estimate", round(sum(costs), 4))
