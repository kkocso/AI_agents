#!/usr/bin/env python3
"""Eval for the plan-validation parser (agents/worker_agent.py).

Every worker's `validate_plan` answer goes through `_parse_validation`,
which decides APPROVE / CONCERN and therefore drives consensus. This eval
feeds raw (synthetic) LLM answers to the parser -- no model, fully
deterministic, runs anywhere -- and measures:

  - accuracy                -- parsed verdict == intended verdict
  - false_approvals         -- intended CONCERN parsed as APPROVE (safety-critical)
  - false_concerns          -- intended APPROVE parsed as CONCERN (costs extra rounds)
  - run_log_rejected        -- answers the run log labels `rejected` (no
                               STATUS verdict found -> safe-default CONCERN)

Case fields: `expected` = what the parser SHOULD return (an answer without a
STATUS verdict should be CONCERN -- that default is deliberate); `intent` =
what the model meant; `well_formed` = a human would say it follows the
requested format (markdown / spacing variants do, but the parser misses them).

Usage (from the repo root):
    python evals/validation_parse/run_eval.py
    python evals/validation_parse/run_eval.py --label after-parser-change

Changing `_parse_validation` needs Gabor's approval (CLAUDE.md autonomy
matrix); the PR must show this eval before and after. Every real parsing
problem seen in a session becomes a new (anonymised) case first.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from agents.worker_agent import _parse_validation, validation_is_well_formed  # noqa: E402

# starting targets -- tune here
TARGETS = {"accuracy": 0.90, "false_approvals": 0}


def load_cases(path: Path) -> list:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def run_eval(cases: list) -> dict:
    rows, by_tag = [], {}
    for case in cases:
        got = _parse_validation("eval", case["answer"]).status.value
        ok = got == case["expected"]
        rows.append({"id": case["id"], "tag": case.get("tag"), "ok": ok, "got": got,
                     "expected": case["expected"], "intent": case.get("intent"),
                     "run_log_label": "accepted" if validation_is_well_formed(case["answer"])
                     else "rejected"})
        t = by_tag.setdefault(case.get("tag", "-"), {"n": 0, "ok": 0})
        t["n"] += 1
        t["ok"] += ok
    n = len(rows)
    false_approvals = [r["id"] for r in rows if r["expected"] == "concern" and r["got"] == "approve"]
    false_concerns = [r["id"] for r in rows if r["expected"] == "approve" and r["got"] == "concern"]
    acc = round(sum(r["ok"] for r in rows) / n, 3) if n else None
    return {
        "cases": n,
        "correct": sum(r["ok"] for r in rows),
        "accuracy": acc,
        "false_approvals": len(false_approvals),
        "false_approval_ids": false_approvals,
        "false_concerns": len(false_concerns),
        "false_concern_ids": false_concerns,
        "run_log_rejected": sum(1 for r in rows if r["run_log_label"] == "rejected"),
        "targets": TARGETS,
        "targets_met": {"accuracy": acc is not None and acc >= TARGETS["accuracy"],
                        "false_approvals": len(false_approvals) <= TARGETS["false_approvals"]},
        "by_tag": by_tag,
        "failures": [r for r in rows if not r["ok"]],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=str(HERE / "cases.jsonl"))
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--no-write", action="store_true", help="print only, no results file")
    args = ap.parse_args(argv)

    res = run_eval(load_cases(Path(args.cases)))
    res = {"date": dt.date.today().isoformat(), "label": args.label,
           "parser": "agents/worker_agent.py:_parse_validation", **res}
    print(f"cases {res['cases']} | accuracy {res['accuracy']} | false approvals "
          f"{res['false_approvals']} {res['false_approval_ids']} | false concerns "
          f"{res['false_concerns']} {res['false_concern_ids']} | run-log rejected {res['run_log_rejected']}")
    if not args.no_write:
        out = HERE / "results" / f"{res['date']}_{args.label}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"-> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
