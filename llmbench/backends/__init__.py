"""Inference backends: map a backend name to its client class."""

from .base import RunMetrics, Backend
from .ollama import OllamaBackend
from .lmstudio import LMStudioBackend

_REGISTRY = {
    "ollama": OllamaBackend,
    "lmstudio": LMStudioBackend,
}


def make_backend(name: str, base_url: str, timeout: int) -> Backend:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise ValueError(f"no backend registered for {name!r}") from None
    return cls(base_url=base_url, timeout=timeout)


__all__ = ["RunMetrics", "Backend", "make_backend"]
