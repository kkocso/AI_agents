"""TrackedBackend: outcomes, tokens (incl. cache), cost, prompt versioning."""

import asyncio
import json

import pytest

from agents.orchestrator import OrchestratorAgent
from agents.worker_agent import WorkerAgent
from backends.tracked import NO_ANSWER_TEXTS, TrackedBackend, estimate_cost, persona_hash
from network.message import ApprovalStatus, ChatMessage
from observability import PRICE_TABLE
from tests.fakes import FakeBackend
from vendor.run_logger import RunLogger

PERSONA = "You are a SECRET-PERSONA architect."


def _calls(run):
    return [json.loads(l) for l in (run.dir / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()]


def _worker(run, inner):
    return WorkerAgent("Secret Name", PERSONA, TrackedBackend(inner, run, "worker", "worker_1"))


def test_accepted_with_prompt_id_version_and_tokens(tmp_path):
    with RunLogger("t", runs_dir=tmp_path) as run:
        inner = FakeBackend(usage={"input": 120, "output": 9, "cache_read": 50, "cache_creation": 7})
        asyncio.run(_worker(run, inner).give_feedback("task"))
    c = _calls(run)[0]
    assert c["outcome"] == "accepted"
    assert c["gen_ai.provider.name"] == "fake" and c["gen_ai.request.model"] == "fake-model"
    assert c["prompt.id"] == "worker.give_feedback"
    assert c["prompt.version"] == f"1+{persona_hash(PERSONA)}"
    assert c["prompt.template_version"] == "1"
    assert c["gen_ai.usage.input_tokens"] == 120 and c["gen_ai.usage.output_tokens"] == 9
    assert c["gen_ai.usage.cache_read_input_tokens"] == 50
    assert c["gen_ai.usage.cache_creation_input_tokens"] == 7
    assert c["agent.slot"] == "worker_1"
    assert c["latency_ms"] is not None


@pytest.mark.parametrize("text", sorted(NO_ANSWER_TEXTS) + ["", "   "])
def test_placeholder_answer_is_no_answer(tmp_path, text):
    with RunLogger("t", runs_dir=tmp_path) as run:
        out = asyncio.run(_worker(run, FakeBackend(answer=text)).chat("hi"))
    assert out == text  # behaviour unchanged: placeholder still returned
    assert _calls(run)[0]["outcome"] == "no_answer"


@pytest.mark.parametrize("exc", [RuntimeError("Ollama request failed (is Ollama running?)"),
                                 ConnectionError(), TimeoutError()])
def test_unreachable_is_unavailable_and_reraised(tmp_path, exc):
    with RunLogger("t", runs_dir=tmp_path) as run:
        with pytest.raises(type(exc)):
            asyncio.run(_worker(run, FakeBackend(exc=exc)).give_feedback("t"))
    c = _calls(run)[0]
    assert c["outcome"] == "unavailable" and c["error.type"] == type(exc).__name__


@pytest.mark.parametrize("answer,status,outcome", [
    ("STATUS: APPROVE\nREASONING: ok", ApprovalStatus.APPROVE, "accepted"),
    ("STATUS: CONCERN\nREASONING: no", ApprovalStatus.CONCERN, "accepted"),
    ("I think it is fine overall.", ApprovalStatus.CONCERN, "rejected"),
    ("STATUS: maybe\nREASONING: dunno", ApprovalStatus.CONCERN, "rejected"),
    ("(Empty response from Ollama.)", ApprovalStatus.CONCERN, "no_answer"),
])
def test_validation_outcomes_keep_default_concern(tmp_path, answer, status, outcome):
    with RunLogger("t", runs_dir=tmp_path) as run:
        v = asyncio.run(_worker(run, FakeBackend(validate_answers=[answer]))
                        .validate_plan("t", "plan", "fb"))
    assert v.status == status
    c = _calls(run)[0]
    assert c["prompt.id"] == "worker.validate_plan" and c["outcome"] == outcome


def test_orchestrator_prompt_ids(tmp_path):
    with RunLogger("t", runs_dir=tmp_path) as run:
        o = OrchestratorAgent("O", PERSONA, TrackedBackend(FakeBackend(), run, "orchestrator", "orchestrator"))
        asyncio.run(o.synthesize_plan("t", [("A", "fb")]))
        asyncio.run(o.refine_plan("t", "p", [("A", "fb")], []))
        asyncio.run(o.chat("hello"))
    ids = [(c["prompt.id"], c["prompt.template_version"]) for c in _calls(run)]
    assert ids == [("orchestrator.synthesize_plan", "1"), ("orchestrator.refine_plan", "1"),
                   ("orchestrator.chat", "0")]


def test_cost_includes_cache_read_and_creation():
    price = PRICE_TABLE["claude-sonnet-4-6"]
    usd = estimate_cost(price, {"input": 1_000_000, "output": 0, "cache_read": 1_000_000,
                                "cache_creation": 1_000_000})
    assert usd == pytest.approx(3.00 + 0.30 + 3.75)
    assert estimate_cost(None, {"input": 5}) is None


def test_claude_model_gets_cost(tmp_path):
    with RunLogger("t", runs_dir=tmp_path, price_table=PRICE_TABLE) as run:
        inner = FakeBackend(model="claude-sonnet-4-6", usage={"input": 1000, "output": 1000})
        asyncio.run(_worker(run, inner).give_feedback("t"))
    assert _calls(run)[0]["cost_usd_estimate"] == pytest.approx((1000 * 3 + 1000 * 15) / 1e6)


def test_untracked_agent_calls_backend_directly():
    inner = FakeBackend()
    w = WorkerAgent("n", PERSONA, inner)
    asyncio.run(w.give_feedback("t"))
    assert inner.sent[0][0] == PERSONA


def test_tracked_backend_is_a_basebackend(tmp_path):
    with RunLogger("t", runs_dir=tmp_path) as run:
        tb = TrackedBackend(FakeBackend(), run, "worker")
        assert asyncio.run(tb.generate("sys", [ChatMessage("user", "x")])) == "ok answer"
    assert _calls(run)[0]["prompt.id"] == "worker.generate"


def test_backend_usage_extraction():
    from types import SimpleNamespace as NS

    from backends.claude_backend import _usage
    resp = NS(model="claude-sonnet-4-6", usage=NS(input_tokens=10, output_tokens=5,
                                                  cache_read_input_tokens=300,
                                                  cache_creation_input_tokens=0))
    assert _usage(resp) == {"input": 10, "output": 5, "cache_read": 300, "cache_creation": 0,
                            "response_model": "claude-sonnet-4-6"}
