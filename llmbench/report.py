"""Build one Markdown report comparing every machine's results.

Two views, both fed by loader.py so they stay consistent with the dashboard:
- Cross-machine comparison: machines side by side, best per row bolded. Numbers
  are pooled across all of a machine's re-run sessions.
- Per-machine deep dive: how each machine's numbers move across its re-runs.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from .loader import (
    METRICS,
    Machine,
    all_cell_keys,
    all_models,
    group_by_machine,
    load_sessions,
)


def _fmt(x: Optional[float], digits: int = 1) -> str:
    return f"{x:.{digits}f}" if isinstance(x, (int, float)) else "—"


def _median(summary: Optional[Dict], metric: str) -> Optional[float]:
    if summary and summary.get(metric):
        return summary[metric].get("median")
    return None


def _hardware_table(machines: List[Machine]) -> List[str]:
    lines = ["## Machines", "",
             "| Machine | Sessions | OS / Arch | CPU | RAM | GPU |",
             "|---|---|---|---|---|---|"]
    for m in machines:
        hw = m.hw
        gpus = ", ".join(hw.get("gpus", []) or ["—"])
        lines.append(
            f"| {m.label} | {len(m.sessions)} | {hw.get('os','—')} "
            f"{hw.get('arch','')} | {hw.get('cpu','—')} | "
            f"{_fmt(hw.get('ram_gb'),0)} GB | {gpus} |"
        )
    lines.append("")
    lines.append(f"<sub>Results folder slug per machine: "
                 + ", ".join(f"`{m.slug}`" for m in machines) + "</sub>")
    lines.append("")
    return lines


def _comparison_table(
    machines: List[Machine], keys, metric: str, label: str,
    higher_is_better: bool, digits: int,
) -> List[str]:
    names = [m.label for m in machines]
    header = ("| Backend | Model | Format | Size | Prompt | "
              + " | ".join(names) + " |")
    sep = "|" + "---|" * (5 + len(names))
    lines = [f"### {label} — median across re-runs", "", header, sep]

    for key in keys:
        backend, model, fmt, prompt_id = key
        values: List[Optional[float]] = []
        size = None
        for m in machines:
            summ = m.pooled_summary(key)
            values.append(_median(summ, metric))
            size = size or m.size_for(key)

        present = [v for v in values if v is not None]
        best = (max(present) if higher_is_better else min(present)) if present else None

        rendered = []
        for v in values:
            if v is None:
                rendered.append("—")
                continue
            text = _fmt(v, digits)
            if best is not None and v == best and len(present) > 1:
                text = f"**{text}**"
            rendered.append(text)

        lines.append(f"| {backend} | {model} | {fmt} | {size or '—'} | "
                     f"{prompt_id} | " + " | ".join(rendered) + " |")
    lines.append("")
    return lines


def _deep_dive(machines: List[Machine], keys) -> List[str]:
    """Per-machine, per-cell breakdown of each re-run session."""
    lines = ["## Per-machine deep dive", "",
             "Each machine's cells across its re-run sessions. `spread` is the "
             "pooled stdev of generation speed — low means the machine is "
             "consistent run to run.", ""]
    for m in machines:
        lines.append(f"### {m.label}")
        lines.append("")
        lines.append(f"<sub>slug `{m.slug}` · {len(m.sessions)} session(s)</sub>")
        lines.append("")
        mkeys = [k for k in keys if k in set(m.cell_keys())]
        if not mkeys:
            lines += ["_No measured cells._", ""]
            continue
        lines.append("| Backend | Model | Format | Prompt | Re-runs | Pooled "
                     "gen tok/s (median) | spread (±) | TTFT median (s) |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for key in mkeys:
            backend, model, fmt, prompt_id = key
            runs = m.pooled_runs(key)
            summ = m.pooled_summary(key)
            gen = summ.get("gen_tokens_per_s") if summ else None
            ttft = summ.get("ttft_s") if summ else None
            lines.append(
                f"| {backend} | {model} | {fmt} | {prompt_id} | {len(runs)} | "
                f"{_fmt(gen.get('median') if gen else None,1)} | "
                f"{_fmt(gen.get('stdev') if gen else None,1)} | "
                f"{_fmt(ttft.get('median') if ttft else None,3)} |"
            )
        lines.append("")

        # Session-by-session trend for the headline metric, when re-run.
        if len(m.sessions) > 1:
            lines += _session_trend(m, mkeys)
    return lines


def _session_trend(m: Machine, mkeys) -> List[str]:
    lines = [f"<details><summary>{m.label}: generation speed per session "
             f"(median tok/s)</summary>", ""]
    for key in mkeys:
        backend, model, fmt, prompt_id = key
        sessions = m.session_summaries(key)
        if len(sessions) <= 1:
            continue
        points = []
        for i, s in enumerate(sessions, 1):
            val = _median(s.get("summary"), "gen_tokens_per_s")
            points.append(f"s{i}={_fmt(val,1)}")
        lines.append(f"- `{backend}/{model}/{fmt}/{prompt_id}`: "
                     + " → ".join(points))
    lines += ["", "</details>", ""]
    return lines


def _errors_section(machines: List[Machine]) -> List[str]:
    rows = []
    for m in machines:
        for e in m.errors():
            rows.append(f"| {m.label} | {e['backend']} | {e['model']} | "
                        f"{e['format']} | {e['prompt_id']} | {e['error']} |")
    if not rows:
        return []
    return (["## Errors", "",
             "| Machine | Backend | Model | Format | Prompt | Error |",
             "|---|---|---|---|---|---|"] + rows + [""])


def build_report(results_dir: str, generated_at: Optional[str] = None) -> str:
    machines = group_by_machine(load_sessions(results_dir))
    keys = all_cell_keys(machines)
    models = all_models(machines)
    total_sessions = sum(len(m.sessions) for m in machines)

    stamp = generated_at or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    out: List[str] = [
        "# llmbench report", "",
        f"_Generated {stamp}_  ·  {len(machines)} machine(s), "
        f"{total_sessions} session(s), {len(models)} model(s)", "",
    ]
    out += _hardware_table(machines)

    out += ["## Comparison", "",
            "Higher is better for generation speed; lower is better for TTFT "
            "and total time. **Bold** = best machine in that row. Each number "
            "pools every re-run on that machine.", ""]
    for metric, label, hib, digits in METRICS:
        out += _comparison_table(machines, keys, metric, label, hib, digits)

    out += _deep_dive(machines, keys)
    out += _errors_section(machines)

    out += ["---", "",
            "<sub>Generation speed is completion tokens over the decode window "
            "(excludes prompt processing and TTFT). Regenerate with "
            "`llmbench report`; interactive charts via `llmbench dashboard`."
            "</sub>", ""]
    return "\n".join(out)
