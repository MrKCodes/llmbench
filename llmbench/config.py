"""Load and validate the sweep YAML into plain dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

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
    # backend name -> model identifier that backend uses
    ids: Dict[str, str] = field(default_factory=dict)


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


def load(path: str) -> Config:
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
        ids = {k: m[k] for k in _KNOWN_BACKENDS if k in m}
        if not ids:
            raise ValueError(
                f"model {m.get('label')!r} has no backend identifiers "
                f"(expected at least one of {sorted(_KNOWN_BACKENDS)})"
            )
        models.append(ModelCfg(label=m["label"], size=m.get("size"), ids=ids))

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
