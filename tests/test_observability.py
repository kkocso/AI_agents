"""Session run log: run.json shape, counts, stages, metrics, privacy guard,
and unchanged behaviour with run=None."""

import asyncio
import builtins
import json
import sys

import pytest

import agents.parser
import main as cli_main
from network.network import AgentNetwork
from observability import PRICE_TABLE, TARGETS, record_metrics
from tests.fakes import FakeBackend, FakeCLI
from vendor.run_logger import RunLogger

SECRETS = ["ACME-SECRET-TASK", "PERSONA-SECRET-PROMPT", "Secret Persona Name",
           "secret_persona_file", "SECRET-PLAN-TEXT", "SECRET-HUMAN-NOTE", "SECRET-CHAT-MSG"]


def _personas(tmp_path):
    d = tmp_path / "personas"
    d.mkdir()
    orch = d / "secret_persona_file_orch.md"
    orch.write_text("---\nname: Secret Persona Name\nrole: orchestrator\n---\n"
                    "PERSONA-SECRET-PROMPT orchestrator", encoding="utf-8")
    w1 = d / "secret_persona_file_w1.md"
    w1.write_text("---\nname: Alice\nrole: worker\n---\nPERSONA-SECRET-PROMPT w1", encoding="utf-8")
    w2 = d / "w2.md"
    w2.write_text("---\nname: Bob\nrole: worker\n---\nPERSONA-SECRET-PROMPT w2", encoding="utf-8")
    return [str(orch), str(w1), str(w2)]


@pytest.fixture
def fakes(monkeypatch):
    """Every agent gets a FakeBackend; round-1 validations: 1 approve, 1 malformed."""
    made = []
    scripts = [None,  # orchestrator
               ["STATUS: APPROVE\nREASONING: ok", "STATUS: APPROVE\nREASONING: ok"],
               ["Looks good to me SECRET-PLAN-TEXT", "STATUS: APPROVE\nREASONING: ok"]]

    def create(backend_type, model):
        fb = FakeBackend(model=model, validate_answers=scripts[len(made)],
                         answer="SECRET-PLAN-TEXT answer")
        made.append(fb)
        return fb
    monkeypatch.setattr(agents.parser, "_create_backend", create)
    return made


def _network(run, max_rounds=3):
    return AgentNetwork("ollama", "llama3.2", "all", max_rounds, run=run)


def _drive(net, tmp_path):
    net.load_agents(_personas(tmp_path))
    cli = FakeCLI(inputs=["SECRET-HUMAN-NOTE", "", ""])  # note before synthesis, none in rounds
    return asyncio.run(net.run_task("ACME-SECRET-TASK", cli))


def _blob(run_dir):
    return "".join(p.read_text(encoding="utf-8") for p in run_dir.iterdir())


def test_run_task_counts_stages_and_privacy(tmp_path, fakes):
    with RunLogger("ai-agents", runs_dir=tmp_path / "runs", price_table=PRICE_TABLE, lang="en") as run:
        net = _network(run)
        plan, dissatisfied = _drive(net, tmp_path)
        record_metrics(run, net)
    assert dissatisfied == []
    data = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in data["stages"]] == [
        "feedback_round", "synthesis", "validation_round_1", "refine_round_1", "validation_round_2"]
    c = data["counts"]
    assert c["tasks"] == 1 and c["rounds_used"] == 2 and c["consensus_reached"] == 1
    assert c["validation.round_1.approvals"] == 1 and c["validation.round_1.concerns"] == 1
    assert c["validation.round_2.approvals"] == 2 and c["unresolved_concerns"] == 0
    assert c["human_notes"] == 1 and c["validations.malformed"] == 1
    m = data["metrics"]
    assert m["rounds_to_consensus"]["value"] == 2 and m["rounds_to_consensus"]["met"] is True
    assert m["malformed_validation_share"]["value"] == 0.25
    assert m["malformed_validation_share"]["label"] == TARGETS["malformed_validation_share"]["label"]
    assert m["ai_unavailable_share"]["value"] == 0 and m["ai_no_answer_share"]["value"] == 0
    assert "duration_s" in m and "cost_usd_estimate" not in m  # ollama: no price
    assert data["llm"]["calls"] == 2 + 1 + 2 + 1 + 2
    kinds = [(e["kind"], e["actor"]) for e in data["events"]]
    assert ("task_submitted", "human") in kinds and ("human_note", "human") in kinds
    # the malformed validation (0.25 > 0.05) misses its target
    assert m["malformed_validation_share"]["met"] is False
    assert data["status"] == "success_with_misses"
    calls = [json.loads(l) for l in (run.dir / "llm_calls.jsonl").read_text().splitlines()]
    assert sorted({c["outcome"] for c in calls}) == ["accepted", "rejected"]
    blob = _blob(run.dir)
    for s in SECRETS:
        assert s not in blob, s


def test_no_consensus_counts_unresolved(tmp_path, monkeypatch):
    monkeypatch.setattr(agents.parser, "_create_backend",
                        lambda t, m: FakeBackend(validate_answers=["STATUS: CONCERN\nREASONING: x"] * 5))
    with RunLogger("t", runs_dir=tmp_path / "runs") as run:
        net = _network(run, max_rounds=2)
        _, dissatisfied = _drive(net, tmp_path)
        record_metrics(run, net)
    data = json.loads((run.dir / "run.json").read_text())
    assert len(dissatisfied) == 2
    assert data["counts"]["tasks_without_consensus"] == 1
    assert data["counts"]["unresolved_concerns"] == 2 and data["counts"]["rounds_used"] == 2
    assert "rounds_to_consensus" not in data["metrics"]


def test_run_none_sends_identical_prompts(tmp_path, monkeypatch):
    """Without a run log the agents send byte-identical prompts and get the same result."""
    def collect(run, sub):
        made = []
        scripts = iter([None, ["bad", "STATUS: APPROVE"], ["STATUS: APPROVE", "STATUS: APPROVE"]])

        def create(t, m):
            fb = FakeBackend(validate_answers=next(scripts))
            made.append(fb)
            return fb
        monkeypatch.setattr(agents.parser, "_create_backend", create)
        d = tmp_path / sub
        d.mkdir()
        result = _drive(_network(run), d)
        return result, [fb.sent for fb in made]

    plain = collect(None, "a")
    with RunLogger("t", runs_dir=tmp_path / "runs") as run:
        tracked = collect(run, "b")
    assert plain == tracked


def test_debug_trace_keeps_text(tmp_path, fakes):
    with RunLogger("t", runs_dir=tmp_path / "runs", debug_trace=True) as run:
        _drive(_network(run), tmp_path)
    assert "ACME-SECRET-TASK" in (run.dir / "llm_calls.jsonl").read_text()


def test_unavailable_backend_logs_failed_session(tmp_path, monkeypatch):
    monkeypatch.setattr(agents.parser, "_create_backend",
                        lambda t, m: FakeBackend(exc=RuntimeError("Ollama request failed")))
    with pytest.raises(RuntimeError):
        with RunLogger("t", runs_dir=tmp_path / "runs") as run:
            net = _network(run)
            try:
                _drive(net, tmp_path)
            finally:
                record_metrics(run, net)
    data = json.loads((run.dir / "run.json").read_text())
    assert data["status"] == "failed" and data["error"] == {"type": "RuntimeError"}
    assert data["metrics"]["ai_unavailable_share"]["value"] == 1.0
    assert data["metrics"]["ai_unavailable_share"]["met"] is False


def test_main_session_end_to_end(tmp_path, fakes, monkeypatch, capsys):
    """python main.py ...: task, chat to @all / @agent / unknown, quit -> one clean run log."""
    agents_files = _personas(tmp_path)
    monkeypatch.setenv("RUN_LOGGER_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(sys, "argv", ["main.py", "--agents", *agents_files])
    answers = iter(["ACME-SECRET-TASK", "", "", "",  # task, note, round notes
                    "@all SECRET-CHAT-MSG", "@alice SECRET-CHAT-MSG", "@nobody hi",
                    "SECRET-CHAT-MSG", "quit"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    cli_main.main()  # returns normally (exit code 0)
    (run_dir,) = list((tmp_path / "runs").iterdir())
    data = json.loads((run_dir / "run.json").read_text())
    assert data["status"] == "success_with_misses" and data["tool"] == "ai-agents"
    names = [s["name"] for s in data["stages"]]
    assert names[0] == "task_input" and names[-1] == "chat_mode"
    c = data["counts"]
    assert c["agents.workers"] == 2 and c["agents.orchestrator"] == 1 and c["rounds_max"] == 5
    assert c["chat_turns"] == 4 and c["chat_replies"] == 3 + 1 + 3
    targets = [e["target"] for e in data["events"] if e["kind"] == "chat_turn"]
    assert targets == ["all", "agent", "unknown_agent", "all"]
    assert ("chat_reply", "llm") in [(e["kind"], e["actor"]) for e in data["events"]]
    blob = _blob(run_dir)
    for s in SECRETS:
        assert s not in blob, s
    assert "Run log:" in capsys.readouterr().out


def test_private_paths_are_gitignored():
    from pathlib import Path
    lines = (Path(__file__).resolve().parents[1] / ".gitignore").read_text().splitlines()
    assert "runs/" in lines and "personas/" in lines and "walkthrough.md" in lines


def test_vendored_run_logger_version():
    import vendor.run_logger as rl
    assert rl.__version__ == "1.2.0"
