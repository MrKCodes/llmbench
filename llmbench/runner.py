"""Drive the sweep: for each (backend, model, prompt), warm up then measure.

Produces the results dict that gets written to JSON and later read by report.py.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from . import hardware
from .backends import make_backend
from .config import Config
from .stats import summarize


def _aggregate(runs: List[Dict]) -> Dict:
    """Roll up per-run metrics into summary stats per metric."""
    return {
        "gen_tokens_per_s": summarize([r["gen_tokens_per_s"] for r in runs]),
        "ttft_s": summarize([r["ttft_s"] for r in runs]),
        "total_s": summarize([r["total_s"] for r in runs]),
        "completion_tokens": summarize([r["completion_tokens"] for r in runs]),
    }


def run_sweep(
    config: Config,
    machine_label: str,
    save_output: bool = False,
    log=print,
) -> Dict:
    """Execute the whole sweep and return a results dict.

    `log` is a callable for progress lines (defaults to print); pass a no-op to
    silence. `save_output` keeps the generated text of the first measured run
    per cell (useful for eyeballing quality; off by default to keep JSON small).
    """
    hw = hardware.detect(machine_label)
    log(f"Machine: {hw['label']}  |  {hw['os']} {hw['arch']}  |  "
        f"CPU: {hw['cpu']}  |  RAM: {hw['ram_gb']} GB  |  GPU: {', '.join(hw['gpus'])}")

    started = time.time()
    cells: List[Dict] = []

    for backend_cfg in config.enabled_backends():
        backend = make_backend(
            backend_cfg.name, backend_cfg.base_url, config.timeout
        )
        reachable = backend.health()
        log(f"\nBackend {backend_cfg.name} @ {backend_cfg.base_url} "
            f"-> {'reachable' if reachable else 'UNREACHABLE (skipping)'}")
        if not reachable:
            continue

        for model in config.models:
            model_id = model.ids.get(backend_cfg.name)
            if not model_id:
                continue  # this model not defined for this backend

            for prompt in config.prompts:
                label = f"{backend_cfg.name} / {model.label} / {prompt.id}"
                log(f"  {label}: warmup x{config.warmup}, measure x{config.runs}")

                error: Optional[str] = None
                measured: List[Dict] = []
                try:
                    for _ in range(config.warmup):
                        backend.generate(
                            model_id, prompt.text,
                            config.temperature, config.max_tokens,
                        )
                    for i in range(config.runs):
                        m = backend.generate(
                            model_id, prompt.text,
                            config.temperature, config.max_tokens,
                        )
                        d = m.to_dict()
                        if not (save_output and i == 0):
                            d.pop("output_text", None)
                        measured.append(d)
                        tps = m.gen_tokens_per_s
                        log(f"    run {i+1}/{config.runs}: "
                            f"{m.completion_tokens} tok, "
                            f"{tps:.1f} tok/s" if tps else
                            f"    run {i+1}/{config.runs}: {m.completion_tokens} tok")
                except Exception as exc:  # noqa: BLE001 - record, don't crash sweep
                    error = f"{type(exc).__name__}: {exc}"
                    log(f"    ERROR: {error}")

                cells.append({
                    "backend": backend_cfg.name,
                    "model": model.label,
                    "model_id": model_id,
                    "size": model.size,
                    "prompt_id": prompt.id,
                    "runs": measured,
                    "summary": _aggregate(measured) if measured else None,
                    "error": error,
                })

    return {
        "schema_version": 1,
        "machine": hw,
        "config": {
            "runs": config.runs,
            "warmup": config.warmup,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "timeout": config.timeout,
        },
        "started_unix": started,
        "finished_unix": time.time(),
        "cells": cells,
    }
