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


def _ids_match(requested: str, other: str) -> bool:
    """True if two model ids refer to the same model, tolerant of prefixes.

    Backends often normalize an id (drop the publisher prefix, lowercase), so
    `google/gemma-3-4b-it` and `gemma-3-4b-it` are the same model. But distinct
    variants must stay distinct: `gemma-3-4b-it` (GGUF) and
    `gemma-3-4b-it-4bit` (MLX) share a prefix yet are different models, so we
    compare the full final path segment, not a prefix.
    """
    if requested is None or other is None:
        return False
    a = requested.strip().lower()
    b = other.strip().lower()
    if a == b:
        return True
    return a.split("/")[-1] == b.split("/")[-1]


def _model_available(model_id: str, available: Optional[List[str]]) -> Optional[bool]:
    """Is model_id served by the backend? None if the list couldn't be read."""
    if available is None:
        return None
    return any(_ids_match(model_id, a) for a in available)


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

        # Ask the server which models it actually has loaded/available. Some
        # backends (LM Studio) silently serve the one loaded model when asked
        # for an unknown id and return 200 — so we must gate on this list, not
        # trust the request. None means the list couldn't be read; we fall back
        # to a per-response served-model check in that case.
        available = backend.available_models()
        if available is not None:
            log(f"  available models ({len(available)}): "
                + (", ".join(available) if available else "(none loaded)"))

        for model in config.models:
            # Each (format, id) variant is benchmarked separately so that, e.g.,
            # the GGUF and MLX builds of a model on a Mac never pool together.
            for fmt, model_id in model.variants.get(backend_cfg.name, []):
                # Gate: skip a variant the server does not actually serve, so we
                # never record a result produced by a substituted model.
                avail = _model_available(model_id, available)
                gated = avail is False

                for prompt in config.prompts:
                    label = (f"{backend_cfg.name} / {model.label} / {fmt} / "
                             f"{prompt.id}")

                    if gated:
                        msg = (f"model not available on server: {model_id!r} "
                               f"not in loaded models")
                        log(f"  {label}: SKIP ({msg})")
                        cells.append({
                            "backend": backend_cfg.name,
                            "model": model.label, "format": fmt,
                            "model_id": model_id, "size": model.size,
                            "prompt_id": prompt.id, "runs": [],
                            "summary": None, "error": msg,
                        })
                        continue

                    log(f"  {label}: warmup x{config.warmup}, "
                        f"measure x{config.runs}")

                    error: Optional[str] = None
                    measured: List[Dict] = []
                    served_seen: Optional[str] = None
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
                            # Defense in depth: if the backend reports which
                            # model answered and it isn't the one we asked for,
                            # the server substituted a different model. Reject.
                            if (m.served_model
                                    and not _ids_match(model_id, m.served_model)):
                                raise RuntimeError(
                                    f"backend served {m.served_model!r} when "
                                    f"asked for {model_id!r} — model not loaded; "
                                    f"result discarded"
                                )
                            served_seen = m.served_model or served_seen
                            d = m.to_dict()
                            if not (save_output and i == 0):
                                d.pop("output_text", None)
                            measured.append(d)
                            tps = m.gen_tokens_per_s
                            log(f"    run {i+1}/{config.runs}: "
                                f"{m.completion_tokens} tok, "
                                f"{tps:.1f} tok/s" if tps else
                                f"    run {i+1}/{config.runs}: "
                                f"{m.completion_tokens} tok")
                    except Exception as exc:  # noqa: BLE001 - record, continue
                        error = f"{type(exc).__name__}: {exc}"
                        measured = []  # discard partials from a substituted model
                        log(f"    ERROR: {error}")

                    cells.append({
                        "backend": backend_cfg.name,
                        "model": model.label,
                        "format": fmt,
                        "model_id": model_id,
                        "served_model": served_seen,
                        "size": model.size,
                        "prompt_id": prompt.id,
                        "runs": measured,
                        "summary": _aggregate(measured) if measured else None,
                        "error": error,
                    })

    return {
        "schema_version": 2,
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
