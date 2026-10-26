"""Load and validate the sweep YAML into plain dataclasses."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml


@dataclass
class BackendCfg:
    name: str
    base_url: str
    enabled: bool = True


@dataclass
class ModelCfg:
    label: str
    size: Optional[str] = None
    # backend name -> list of (format, model_identifier) variants.
    # One model can be benchmarked in several on-disk formats on the same
    # backend — e.g. the same 14B model as GGUF and as MLX in LM Studio on a
    # Mac. Each (format, id) becomes its own cell so the two never pool together.
    variants: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)


@dataclass
class PromptCfg:
    id: str
    text: str


@dataclass
class Config:
    runs: int
    warmup: int
    max_tokens: Optional[int]
    temperature: float
    timeout: int
    backends: List[BackendCfg]
    models: List[ModelCfg]
    prompts: List[PromptCfg]

    def enabled_backends(self) -> List[BackendCfg]:
        return [b for b in self.backends if b.enabled]


_KNOWN_BACKENDS = {"ollama", "lmstudio"}

# Format each backend serves unless a variant says otherwise. Ollama is always
# GGUF; LM Studio can serve both, so a bare string there is assumed GGUF and MLX
# must be stated explicitly.
_DEFAULT_FORMAT = {"ollama": "gguf", "lmstudio": "gguf"}


def _parse_variants(backend: str, value) -> List[Tuple[str, str]]:
    """Normalize a model's per-backend value into [(format, id), ...].

    Accepts either a bare string (format inferred from the backend default) or
    a list of {format, id} mappings for benchmarking the same model in several
    formats on one backend (e.g. GGUF and MLX in LM Studio).
    """
    if isinstance(value, str):
        return [(_DEFAULT_FORMAT.get(backend, "gguf"), value)]
    if isinstance(value, list):
        out = []
        for item in value:
            if not isinstance(item, dict) or "id" not in item:
                raise ValueError(
                    f"backend {backend!r} variant must be a mapping with an "
                    f"'id' (and optional 'format'); got {item!r}"
                )
            fmt = str(item.get("format", _DEFAULT_FORMAT.get(backend, "gguf"))).lower()
            out.append((fmt, item["id"]))
        return out
    raise ValueError(
        f"backend {backend!r} value must be a string or a list of "
        f"{{format, id}} mappings; got {type(value).__name__}"
    )


def load(path: str) -> Config:
    if not os.path.exists(path):
        # The tracked file is config/bench.sample.yaml; users copy it to their
        # own working config so sweeps never overwrite the default.
        sample = os.path.join(os.path.dirname(path) or ".", "bench.sample.yaml")
        hint = (f"\nCopy the sample to start: cp {sample} {path}"
                if os.path.exists(sample) else "")
        raise FileNotFoundError(f"config not found: {path}{hint}")

    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")

    backends = []
    for b in raw.get("backends", []):
        name = b["name"]
        if name not in _KNOWN_BACKENDS:
            raise ValueError(
                f"unknown backend {name!r}; known: {sorted(_KNOWN_BACKENDS)}"
            )
        backends.append(
            BackendCfg(
                name=name,
                base_url=b["base_url"].rstrip("/"),
                enabled=bool(b.get("enabled", True)),
            )
        )

    models = []
    for m in raw.get("models", []):
        variants = {
            k: _parse_variants(k, m[k]) for k in _KNOWN_BACKENDS if k in m
        }
        if not variants:
            raise ValueError(
                f"model {m.get('label')!r} has no backend identifiers "
                f"(expected at least one of {sorted(_KNOWN_BACKENDS)})"
            )
        models.append(
            ModelCfg(label=m["label"], size=m.get("size"), variants=variants)
        )

    prompts = [PromptCfg(id=p["id"], text=p["text"].strip()) for p in raw.get("prompts", [])]

    if not backends:
        raise ValueError(f"{path}: no backends defined")
    if not models:
        raise ValueError(f"{path}: no models defined")
    if not prompts:
        raise ValueError(f"{path}: no prompts defined")

    runs = int(raw.get("runs", 5))
    if runs < 1:
        raise ValueError("runs must be >= 1")

    return Config(
        runs=runs,
        warmup=int(raw.get("warmup", 1)),
        max_tokens=raw.get("max_tokens"),
        temperature=float(raw.get("temperature", 0.2)),
        timeout=int(raw.get("timeout", 600)),
        backends=backends,
        models=models,
        prompts=prompts,
    )
