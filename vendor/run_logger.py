"""run_logger — offline futás- és LLM-telemetria minden pipeline-hoz.

VENDOROLT MÁSOLAT. Ne itt szerkeszd: a forrás a
`projects/github/_shared/observability/run_logger/run_logger.py`. Új verziót
onnan másolj be, és a __version__ alapján követhető, melyik projekt melyiket
használja.

Csak a Python standard könyvtárára épül, hálózatot nem használ, ügyfélgépen
offline is fut (Python >= 3.9, Windows/macOS/Linux).

Minden futás egy mappát ír: <runs_dir>/<YYYYMMDD-HHMMSS>_<run_id>/
    run.json         gépi összefoglaló (bemenet-hashek, szakaszok, számlálók,
                     célértékek, LLM-összesítő, státusz)
    llm_calls.jsonl  LLM-hívásonként egy sor, OpenTelemetry GenAI mezőnevekkel
    run_report.md    ember által olvasható riport (ügyfélnek is mutatható)

Adatvédelem: prompt- és válaszszöveget, fájlnevet és hibaüzenetet alapból NEM
naplóz. Ezek csak debug_trace=True (vagy RUN_LOGGER_DEBUG_TRACE=1) mellett
kerülnek a naplóba, és akkor is csak helyben.

A logger soha nem töri el a pipeline-t: minden belső hiba elnyelődik, és
legfeljebb egy figyelmeztetés megy a stderr-re.

Gyors használat:

    from run_logger import RunLogger

    with RunLogger("vsp-build", version="1.4.0", config=cfg) as run:
        run.add_input("input.xlsx", label="EPIC export")
        with run.stage("read"):
            rows = read(...)
            run.count("rows_read", len(rows))
        with run.track_llm(provider="ollama", model="llama3.1:8b",
                           prompt_id="location_normalize", prompt_version="2") as call:
            answer = ask(...)
            call.tokens(input=812, output=14)
            call.outcome("accepted")          # vagy fallback_deterministic / rejected
        run.metric("llm_fallback_rate", 0.04, target=0.10, direction="max")
        run.add_output("out.xlsx", label="VSP workbook")

Környezeti változók:
    RUN_LOGGER_DISABLED=1      semmit nem ír (pl. unit tesztekben)
    RUN_LOGGER_DIR=<útvonal>   a runs_dir felülírása
    RUN_LOGGER_DEBUG_TRACE=1   debug_trace bekapcsolása
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

__version__ = "1.2.0"
SCHEMA_VERSION = "run-log/1"

ACTORS = ("human", "script", "llm", "agent")
# accepted: a válasz felhasználva · no_answer: a modell válaszolt, de nem
# adott használható információt · unavailable: a modell nem volt elérhető
# (kapcsolat, időtúllépés) · rejected: validátor elvetette · error: egyéb hiba
LLM_OUTCOMES = ("accepted", "fallback_deterministic", "rejected", "no_answer",
                "unavailable", "error", "cached")


def _now() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


def _iso(t: _dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _warn(msg: str) -> None:
    try:
        print(f"[run_logger] {msg}", file=sys.stderr)
    except Exception:  # pragma: no cover
        pass


def sha256_file(path: os.PathLike | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_hash(config: Any) -> Optional[str]:
    if config is None:
        return None
    try:
        blob = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        blob = repr(config)
    return sha256_text(blob)[:16]


def _git_info(cwd: Optional[Path] = None) -> Dict[str, Any]:
    info: Dict[str, Any] = {"sha": None, "dirty": None, "branch": None}
    try:
        def git(*args: str) -> str:
            return subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=3, check=True
            ).stdout.strip()

        info["sha"] = git("rev-parse", "--short=12", "HEAD")
        info["branch"] = git("rev-parse", "--abbrev-ref", "HEAD")
        info["dirty"] = bool(git("status", "--porcelain", "--untracked-files=no"))
    except Exception:
        pass
    return info


class LLMCall:
    """Egy LLM-hívás rekordja. track_llm() adja vissza; a tokeneket és a
    kimenet sorsát a hívó tölti ki, az időt a context manager méri."""

    def __init__(self, run: "RunLogger", provider: str, model: str, prompt_id: str,
                 prompt_version: str, operation: str, actor: str) -> None:
        self._run = run
        self.record: Dict[str, Any] = {
            "call_id": uuid.uuid4().hex[:12],
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "gen_ai.operation.name": operation,
            "prompt.id": prompt_id,
            "prompt.version": str(prompt_version),
            "gen_ai.usage.input_tokens": None,
            "gen_ai.usage.output_tokens": None,
            "gen_ai.usage.cache_read_input_tokens": None,
            "latency_ms": None,
            "cost_usd_estimate": None,
            "outcome": None,
            "actor": actor,
            "stage": run._current_stage,
        }

    def tokens(self, input: Optional[int] = None, output: Optional[int] = None,
               cache_read: Optional[int] = None) -> None:
        if input is not None:
            self.record["gen_ai.usage.input_tokens"] = int(input)
        if output is not None:
            self.record["gen_ai.usage.output_tokens"] = int(output)
        if cache_read is not None:
            self.record["gen_ai.usage.cache_read_input_tokens"] = int(cache_read)

    def outcome(self, value: str) -> None:
        if value not in LLM_OUTCOMES:
            _warn(f"ismeretlen outcome: {value!r} (várt: {LLM_OUTCOMES})")
        self.record["outcome"] = value

    def cost(self, usd: float) -> None:
        self.record["cost_usd_estimate"] = round(float(usd), 6)

    def response_model(self, model: str) -> None:
        self.record["gen_ai.response.model"] = model

    def text(self, prompt: Optional[str] = None, response: Optional[str] = None) -> None:
        """A szöveget csak hash-ként és hosszként tárolja, kivéve debug_trace módban."""
        for key, val in (("prompt", prompt), ("response", response)):
            if val is None:
                continue
            self.record[f"{key}.sha256"] = sha256_text(val)[:16]
            self.record[f"{key}.chars"] = len(val)
            if self._run.debug_trace:
                self.record[f"{key}.text"] = val


class RunLogger:
    """Egy pipeline-futás naplója. Context managerként használd."""

    def __init__(self, tool: str, version: Optional[str] = None, *,
                 runs_dir: os.PathLike | str = "runs", config: Any = None,
                 actor: str = "script", debug_trace: Optional[bool] = None,
                 lang: str = "hu", price_table: Optional[Dict[str, Dict[str, float]]] = None,
                 project_root: Optional[os.PathLike | str] = None) -> None:
        self.disabled = _env_flag("RUN_LOGGER_DISABLED")
        self.tool = tool
        self.tool_version = version
        self.actor = actor if actor in ACTORS else "script"
        self.debug_trace = _env_flag("RUN_LOGGER_DEBUG_TRACE") if debug_trace is None else debug_trace
        self.lang = lang if lang in _LABELS else "hu"
        self.price_table = price_table or {}
        self.run_id = uuid.uuid4().hex[:8]
        self.started = _now()
        self.ended: Optional[_dt.datetime] = None
        self._t0 = time.perf_counter()
        base = Path(os.environ.get("RUN_LOGGER_DIR") or runs_dir)
        self.dir = base / f"{self.started.strftime('%Y%m%d-%H%M%S')}_{self.run_id}"
        self._project_root = Path(project_root) if project_root else None
        self._config_hash = config_hash(config)
        self.inputs: List[Dict[str, Any]] = []
        self.outputs: List[Dict[str, Any]] = []
        self.stages: List[Dict[str, Any]] = []
        self.counts: Dict[str, int] = {}
        self.metrics: Dict[str, Dict[str, Any]] = {}
        self.warnings: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.llm_calls: List[Dict[str, Any]] = []
        self.status = "running"
        self.error: Optional[Dict[str, Any]] = None
        self._current_stage: Optional[str] = None
        self._marked: Optional[Dict[str, Any]] = None  # mark_stage() nyitott szakasza

    # ---- context manager -------------------------------------------------
    def __enter__(self) -> "RunLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._end_marked("failed" if exc_type is not None else "ok")
        if exc_type is None:
            self.status = "success" if not self._has_failed_metric() else "success_with_misses"
        else:
            self.status = "failed"
            self.error = {"type": exc_type.__name__}
            if self.debug_trace:
                self.error["message"] = str(exc)
                self.error["traceback"] = "".join(traceback.format_exception(exc_type, exc, tb))
        self.close()
        return False  # a kivételt nem nyeljük el

    # ---- bemenet / kimenet -----------------------------------------------
    def _file_entry(self, path: os.PathLike | str, label: Optional[str]) -> Dict[str, Any]:
        p = Path(path)
        entry: Dict[str, Any] = {"label": label, "suffix": p.suffix.lower()}
        try:
            entry["sha256"] = sha256_file(p)
            entry["bytes"] = p.stat().st_size
        except Exception as e:  # hiányzó fájl ne törje a futást
            entry["sha256"] = None
            entry["missing"] = type(e).__name__
        if self.debug_trace:
            entry["path"] = str(p)
        return entry

    def add_input(self, path: os.PathLike | str, label: Optional[str] = None) -> None:
        self._safe(lambda: self.inputs.append(self._file_entry(path, label)))

    def add_output(self, path: os.PathLike | str, label: Optional[str] = None) -> None:
        self._safe(lambda: self.outputs.append(self._file_entry(path, label)))

    # ---- szakaszok, számlálók, célértékek --------------------------------
    @contextlib.contextmanager
    def stage(self, name: str) -> Iterator[None]:
        prev, self._current_stage = self._current_stage, name
        t0 = time.perf_counter()
        status = "ok"
        try:
            yield
        except BaseException:
            status = "failed"
            raise
        finally:
            self.stages.append({"name": name, "ms": round((time.perf_counter() - t0) * 1000, 1),
                                "status": status})
            self._current_stage = prev

    def mark_stage(self, name: str) -> None:
        """Lineáris szakaszjelölés behúzás nélkül: lezárja az előző mark_stage()
        szakaszt, és megnyitja a következőt. A futás végén az utolsó magától
        lezárul. Hosszú, egyetlen függvényből álló pipeline-okhoz való."""
        self._end_marked("ok")
        self._marked = {"name": name, "t0": time.perf_counter(), "prev": self._current_stage}
        self._current_stage = name

    def _end_marked(self, status: str) -> None:
        m = self._marked
        if not m:
            return
        self.stages.append({"name": m["name"], "ms": round((time.perf_counter() - m["t0"]) * 1000, 1),
                            "status": status})
        self._current_stage = m["prev"]
        self._marked = None

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + int(n)

    def metric(self, name: str, value: float, *, target: Optional[float] = None,
               direction: str = "max", unit: str = "", label: Optional[str] = None) -> None:
        """Célérték rögzítése. direction='max': value <= target a jó (pl. hibaarány);
        direction='min': value >= target a jó (pl. pontosság). A *label* ember által
        olvasható név a riportba (pl. ügyfélnek szóló megfogalmazás)."""
        met = None
        if target is not None:
            met = value <= target if direction == "max" else value >= target
        self.metrics[name] = {"value": value, "target": target, "direction": direction,
                              "unit": unit, "met": met, "label": label}

    def warn(self, message: str, *, where: Optional[str] = None, code: Optional[str] = None) -> None:
        """Figyelmeztetés. A message-be NE kerüljön ügyféladat; használj kódot + helyet."""
        self.warnings.append({"code": code, "where": where, "message": message,
                              "stage": self._current_stage})

    def event(self, kind: str, *, actor: Optional[str] = None, **fields: Any) -> None:
        """Döntés/beavatkozás naplózása (pl. kézi jóváhagyás, felülírás)."""
        a = actor if actor in ACTORS else self.actor
        self.events.append({"ts": _iso(_now()), "kind": kind, "actor": a,
                            "stage": self._current_stage, **fields})

    # ---- LLM-hívások -----------------------------------------------------
    @contextlib.contextmanager
    def track_llm(self, *, provider: str, model: str, prompt_id: str,
                  prompt_version: str | int = "1", operation: str = "chat",
                  actor: str = "llm") -> Iterator[LLMCall]:
        call = LLMCall(self, provider, model, prompt_id, str(prompt_version), operation, actor)
        t0 = time.perf_counter()
        try:
            yield call
        except BaseException as e:
            if call.record["outcome"] is None:
                call.record["outcome"] = "error"
                call.record["error.type"] = type(e).__name__
            raise
        finally:
            call.record["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            if call.record["cost_usd_estimate"] is None:
                call.record["cost_usd_estimate"] = self._estimate_cost(call.record)
            self.llm_calls.append(call.record)

    def log_llm_call(self, **record: Any) -> None:
        """Kész rekord rögzítése, ha a hívás nem context managerrel ment."""
        self.llm_calls.append(record)

    def _estimate_cost(self, rec: Dict[str, Any]) -> Optional[float]:
        price = self.price_table.get(rec.get("gen_ai.request.model") or "")
        if not price:
            return None
        tin = rec.get("gen_ai.usage.input_tokens") or 0
        tout = rec.get("gen_ai.usage.output_tokens") or 0
        tcache = rec.get("gen_ai.usage.cache_read_input_tokens") or 0
        usd = (tin * price.get("input", 0) + tout * price.get("output", 0)
               + tcache * price.get("cache_read", price.get("input", 0))) / 1_000_000
        return round(usd, 6)

    # ---- összesítés, írás ------------------------------------------------
    def _has_failed_metric(self) -> bool:
        return any(m["met"] is False for m in self.metrics.values())

    def llm_summary(self) -> Dict[str, Any]:
        calls = self.llm_calls
        def total(k: str) -> int:
            return sum((c.get(k) or 0) for c in calls)
        by_outcome: Dict[str, int] = {}
        for c in calls:
            o = c.get("outcome") or "unknown"
            by_outcome[o] = by_outcome.get(o, 0) + 1
        costs = [c.get("cost_usd_estimate") for c in calls if c.get("cost_usd_estimate") is not None]
        lat = sorted(c["latency_ms"] for c in calls if c.get("latency_ms") is not None)
        tin = total("gen_ai.usage.input_tokens")
        tcache = total("gen_ai.usage.cache_read_input_tokens")
        return {
            "calls": len(calls),
            "by_outcome": by_outcome,
            "input_tokens": tin,
            "output_tokens": total("gen_ai.usage.output_tokens"),
            "cache_read_input_tokens": tcache,
            "cache_share": round(tcache / (tin + tcache), 3) if (tin + tcache) else None,
            "cost_usd_estimate": round(sum(costs), 6) if costs else None,
            "latency_ms_p50": lat[len(lat) // 2] if lat else None,
            "latency_ms_max": lat[-1] if lat else None,
            "models": sorted({c.get("gen_ai.request.model") or "?" for c in calls}),
        }

    def to_dict(self) -> Dict[str, Any]:
        ended = self.ended or _now()
        return {
            "schema": SCHEMA_VERSION,
            "run_logger_version": __version__,
            "run_id": self.run_id,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "actor": self.actor,
            "status": self.status,
            "error": self.error,
            "started": _iso(self.started),
            "ended": _iso(ended),
            "duration_ms": round((time.perf_counter() - self._t0) * 1000, 1),
            "git": _git_info(self._project_root),
            "host": {"python": platform.python_version(), "os": platform.system()},
            "config_hash": self._config_hash,
            "debug_trace": self.debug_trace,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "stages": self.stages,
            "counts": self.counts,
            "metrics": self.metrics,
            "warnings": {"total": len(self.warnings), "items": self.warnings[:200]},
            "events": self.events,
            "llm": self.llm_summary(),
        }

    def close(self) -> Optional[Path]:
        if self.ended is not None:
            return self.dir
        self._end_marked("ok")
        if self.status == "running":
            self.status = "success" if not self._has_failed_metric() else "success_with_misses"
        self.ended = _now()
        if self.disabled:
            return None
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            data = self.to_dict()
            (self.dir / "run.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            with open(self.dir / "llm_calls.jsonl", "w", encoding="utf-8") as f:
                for rec in self.llm_calls:
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            (self.dir / "run_report.md").write_text(render_report(data, self.lang), encoding="utf-8")
            return self.dir
        except Exception as e:
            _warn(f"a futásnapló írása nem sikerült ({type(e).__name__}); a pipeline fut tovább")
            return None

    def _safe(self, fn) -> None:
        try:
            fn()
        except Exception as e:  # pragma: no cover
            _warn(f"belső hiba ({type(e).__name__}) — kihagyva")


# ---- ember által olvasható riport ----------------------------------------
_LABELS = {
    "hu": {
        "title": "Futási riport", "tool": "Eszköz", "status": "Státusz", "started": "Indítva",
        "duration": "Időtartam", "git": "Kódverzió", "config": "Konfiguráció (hash)",
        "inputs": "Bemenetek", "outputs": "Kimenetek", "stages": "Szakaszok",
        "counts": "Számlálók", "metrics": "Célértékek", "llm": "AI-hívások",
        "warnings": "Figyelmeztetések", "events": "Döntések, beavatkozások",
        "no_llm": "Ebben a futásban nem volt AI-hívás — a teljes eredmény determinisztikus.",
        "met": "teljesült", "missed": "NEM teljesült", "none": "—",
        "calls": "hívás", "tokens": "token (be/ki/cache)", "cost": "becsült költség",
        "latency": "késleltetés p50 / max", "outcomes": "kimenetek",
        "privacy": "A riport ügyféladatot nem tartalmaz: a fájlokat SHA-256 hash azonosítja, "
                   "a prompt- és válaszszövegek nincsenek naplózva.",
        "dirty": "nem commitolt módosításokkal",
    },
    "en": {
        "title": "Run report", "tool": "Tool", "status": "Status", "started": "Started",
        "duration": "Duration", "git": "Code version", "config": "Configuration (hash)",
        "inputs": "Inputs", "outputs": "Outputs", "stages": "Stages",
        "counts": "Counters", "metrics": "Targets", "llm": "AI calls",
        "warnings": "Warnings", "events": "Decisions and interventions",
        "no_llm": "No AI calls in this run — the entire result is deterministic.",
        "met": "met", "missed": "MISSED", "none": "—",
        "calls": "calls", "tokens": "tokens (in/out/cache)", "cost": "estimated cost",
        "latency": "latency p50 / max", "outcomes": "outcomes",
        "privacy": "This report contains no client data: files are identified by SHA-256 hash, "
                   "prompt and response texts are not logged.",
        "dirty": "with uncommitted changes",
    },
}


def _fmt_ms(ms: Optional[float]) -> str:
    if ms is None:
        return "—"
    return f"{ms / 1000:.2f} s" if ms >= 1000 else f"{ms:.0f} ms"


def render_report(d: Dict[str, Any], lang: str = "hu") -> str:
    L = _LABELS.get(lang, _LABELS["hu"])
    out: List[str] = []
    w = out.append
    w(f"# {L['title']} — {d['tool']} {d.get('tool_version') or ''}".rstrip())
    w("")
    g = d.get("git") or {}
    gtxt = g.get("sha") or L["none"]
    if g.get("dirty"):
        gtxt += f" ({L['dirty']})"
    w(f"- **{L['status']}:** {d['status']}" + (f" ({d['error']['type']})" if d.get("error") else ""))
    w(f"- **{L['started']}:** {d['started']}  ·  **{L['duration']}:** {_fmt_ms(d['duration_ms'])}")
    w(f"- **{L['git']}:** {gtxt}  ·  **{L['config']}:** {d.get('config_hash') or L['none']}")
    w(f"- run_id: `{d['run_id']}`")
    w("")
    for key in ("inputs", "outputs"):
        items = d.get(key) or []
        if not items:
            continue
        w(f"## {L[key]}")
        for it in items:
            sha = (it.get("sha256") or "?")[:16]
            w(f"- {it.get('label') or it.get('suffix') or '?'} — `{sha}…` ({it.get('bytes', '?')} B)")
        w("")
    if d.get("stages"):
        w(f"## {L['stages']}")
        w("| | ms | |")
        w("|---|---:|---|")
        for s in d["stages"]:
            w(f"| {s['name']} | {s['ms']:.0f} | {s['status']} |")
        w("")
    if d.get("counts"):
        w(f"## {L['counts']}")
        for k, v in d["counts"].items():
            w(f"- {k}: {v}")
        w("")
    if d.get("metrics"):
        w(f"## {L['metrics']}")
        for k, m in d["metrics"].items():
            tgt = ""
            if m.get("target") is not None:
                sign = "≤" if m.get("direction") == "max" else "≥"
                verdict = L["met"] if m.get("met") else L["missed"]
                tgt = f" (cél {sign} {m['target']}{m.get('unit', '')}: {verdict})" if lang == "hu" \
                    else f" (target {sign} {m['target']}{m.get('unit', '')}: {verdict})"
            name = f"{m['label']} (`{k}`)" if m.get("label") else k
            w(f"- {name}: {m['value']}{m.get('unit', '')}{tgt}")
        w("")
    w(f"## {L['llm']}")
    llm = d.get("llm") or {}
    if not llm.get("calls"):
        w(L["no_llm"])
    else:
        w(f"- {llm['calls']} {L['calls']} · {', '.join(llm.get('models') or [])}")
        w(f"- {L['tokens']}: {llm['input_tokens']} / {llm['output_tokens']} / {llm['cache_read_input_tokens']}")
        if llm.get("cost_usd_estimate") is not None:
            w(f"- {L['cost']}: ${llm['cost_usd_estimate']:.4f}")
        w(f"- {L['latency']}: {_fmt_ms(llm.get('latency_ms_p50'))} / {_fmt_ms(llm.get('latency_ms_max'))}")
        w(f"- {L['outcomes']}: " + ", ".join(f"{k} {v}" for k, v in llm["by_outcome"].items()))
    w("")
    warns = d.get("warnings") or {}
    if warns.get("total"):
        w(f"## {L['warnings']} ({warns['total']})")
        for it in warns["items"][:50]:
            loc = f" [{it['where']}]" if it.get("where") else ""
            code = f"{it['code']}: " if it.get("code") else ""
            w(f"- {code}{it['message']}{loc}")
        w("")
    if d.get("events"):
        w(f"## {L['events']}")
        for e in d["events"]:
            extra = {k: v for k, v in e.items() if k not in ("ts", "kind", "actor", "stage")}
            w(f"- {e['ts']} · {e['actor']} · {e['kind']}" + (f" · {extra}" if extra else ""))
        w("")
    if not d.get("debug_trace"):
        w(f"_{L['privacy']}_")
    return "\n".join(out) + "\n"
