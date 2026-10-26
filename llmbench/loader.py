"""Load result sessions and group them per machine.

A *session* is one `llmbench run` (one JSON file). Re-runs on the same machine
produce multiple session files in that machine's folder. This module is the
single source of truth both the Markdown report and the HTML dashboard read
from, so they never disagree.

Key ideas:
- Machines are identified by their hardware `slug`, not a hand-typed name, so
  every session from the same box groups together automatically.
- A cell is a (backend, model, prompt_id) combination. For cross-machine
  comparison we *pool* the raw per-run measurements across all of a machine's
  sessions and re-summarize — more re-runs means a larger sample, not a
  flattened average-of-averages.
- Per-session summaries are kept too, so the deep dive can show how a machine's
  numbers move across re-runs (consistency / drift).
"""

from __future__ import annotations

import glob
import json
import os
from typing import Dict, List, Optional, Tuple

from .stats import summarize

CellKey = Tuple[str, str, str]  # (backend, model, prompt_id)

# Metrics we track everywhere, with display direction.
METRICS = [
    ("gen_tokens_per_s", "Generation speed (tokens/sec)", True, 1),
    ("ttft_s", "Time to first token (seconds)", False, 3),
    ("total_s", "Total time (seconds)", False, 2),
]


def load_sessions(results_dir: str) -> List[Dict]:
    """Recursively load every result JSON under results_dir, newest last.

    Each returned dict is the on-disk session plus `_path` (relative to
    results_dir) and `_mtime` (file mtime, for ordering re-runs).
    """
    pattern = os.path.join(results_dir, "**", "*.json")
    paths = sorted(glob.glob(pattern, recursive=True))
    sessions = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue  # skip junk; a bad file shouldn't sink the whole report
        if not isinstance(data, dict) or "cells" not in data:
            continue
        data["_path"] = os.path.relpath(p, results_dir)
        try:
            data["_mtime"] = os.path.getmtime(p)
        except OSError:
            data["_mtime"] = 0
        sessions.append(data)
    sessions.sort(key=lambda s: s.get("_mtime", 0))
    if not sessions:
        raise FileNotFoundError(
            f"no result JSON files found under {results_dir} "
            f"(run `llmbench run` first)"
        )
    return sessions


def _machine_slug(session: Dict) -> str:
    hw = session.get("machine", {})
    return hw.get("slug") or hw.get("label") or session["_path"]


def _cell_key(cell: Dict) -> CellKey:
    return (cell["backend"], cell["model"], cell["prompt_id"])


def _pool_summaries(runs: List[Dict]) -> Optional[Dict]:
    """Summarize a pooled list of per-run dicts into one summary block."""
    if not runs:
        return None
    return {
        metric: summarize([r.get(metric) for r in runs])
        for metric, _lbl, _hib, _d in METRICS
    } | {"completion_tokens": summarize([r.get("completion_tokens") for r in runs])}


class Machine:
    """All sessions from one physical machine, with pooled + per-session views."""

    def __init__(self, slug: str):
        self.slug = slug
        self.sessions: List[Dict] = []

    @property
    def hw(self) -> Dict:
        # newest session's hardware profile (hardware shouldn't change, but if
        # RAM/GPU was upgraded the latest reading wins)
        return self.sessions[-1].get("machine", {}) if self.sessions else {}

    @property
    def label(self) -> str:
        return self.hw.get("label") or self.slug

    def cell_keys(self) -> List[CellKey]:
        keys = {}
        for s in self.sessions:
            for c in s.get("cells", []):
                keys[_cell_key(c)] = c.get("size")
        return list(keys)

    def size_for(self, key: CellKey) -> Optional[str]:
        for s in self.sessions:
            for c in s.get("cells", []):
                if _cell_key(c) == key and c.get("size"):
                    return c["size"]
        return None

    def pooled_runs(self, key: CellKey) -> List[Dict]:
        """Every per-run measurement for a cell, across all sessions."""
        runs: List[Dict] = []
        for s in self.sessions:
            for c in s.get("cells", []):
                if _cell_key(c) == key:
                    runs.extend(c.get("runs", []) or [])
        return runs

    def pooled_summary(self, key: CellKey) -> Optional[Dict]:
        return _pool_summaries(self.pooled_runs(key))

    def session_summaries(self, key: CellKey) -> List[Dict]:
        """Per-session view for the deep dive: one entry per re-run.

        Each entry: {path, mtime, summary, n_runs, error}. Ordered oldest→newest.
        """
        out = []
        for s in self.sessions:
            for c in s.get("cells", []):
                if _cell_key(c) == key:
                    runs = c.get("runs", []) or []
                    out.append({
                        "path": s["_path"],
                        "mtime": s.get("_mtime", 0),
                        "summary": c.get("summary") or _pool_summaries(runs),
                        "n_runs": len(runs),
                        "error": c.get("error"),
                    })
        return out

    def errors(self) -> List[Dict]:
        out = []
        for s in self.sessions:
            for c in s.get("cells", []):
                if c.get("error"):
                    out.append({
                        "path": s["_path"], "backend": c["backend"],
                        "model": c["model"], "prompt_id": c["prompt_id"],
                        "error": c["error"],
                    })
        return out


def group_by_machine(sessions: List[Dict]) -> List[Machine]:
    """Bucket sessions into Machines by hardware slug, label order stable."""
    machines: Dict[str, Machine] = {}
    for s in sessions:
        slug = _machine_slug(s)
        machines.setdefault(slug, Machine(slug)).sessions.append(s)
    return sorted(machines.values(), key=lambda m: m.label.lower())


def all_cell_keys(machines: List[Machine]) -> List[CellKey]:
    """Union of every cell across all machines, sorted for stable display."""
    keys = set()
    for m in machines:
        keys.update(m.cell_keys())
    return sorted(keys)


def all_models(machines: List[Machine]) -> Dict[str, Optional[str]]:
    """model label -> size, across all machines."""
    out: Dict[str, Optional[str]] = {}
    for m in machines:
        for (_backend, model, _pid) in m.cell_keys():
            out.setdefault(model, m.size_for((_backend, model, _pid)))
    return out
