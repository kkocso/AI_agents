"""The validation_parse eval scaffold runs deterministically, and guards the
deliberate default-to-CONCERN behaviour of _parse_validation."""

import json
from pathlib import Path

import pytest

from agents.worker_agent import _parse_validation, validation_is_well_formed
from network.message import ApprovalStatus

EVAL = Path(__file__).resolve().parents[1] / "evals" / "validation_parse"
CASES = [json.loads(l) for l in (EVAL / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def test_eval_runs_without_writing(capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_eval", EVAL / "run_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--no-write"]) == 0
    res = mod.run_eval(CASES)
    assert res["cases"] == len(CASES) >= 30
    assert "accuracy" in capsys.readouterr().out


@pytest.mark.parametrize("case", [c for c in CASES if c["tag"] == "canonical"], ids=lambda c: c["id"])
def test_canonical_answers_parse(case):
    assert _parse_validation("a", case["answer"]).status.value == case["expected"]


@pytest.mark.parametrize("case", [c for c in CASES if not c["well_formed"]], ids=lambda c: c["id"])
def test_answers_without_status_default_to_concern(case):
    """Design decision to preserve: ambiguous/malformed -> CONCERN."""
    assert _parse_validation("a", case["answer"]).status == ApprovalStatus.CONCERN
    assert validation_is_well_formed(case["answer"]) is False
