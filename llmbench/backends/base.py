"""Shared types for backends.

A backend turns one prompt into one `RunMetrics`. All backends stream the
response so that time-to-first-token (TTFT) is a real measurement, not an
after-the-fact guess.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Dict, List


@dataclass
class RunMetrics:
    """Timings and token counts for a single generation.

    Times are seconds. Token counts come from the backend when it reports them;
    `prompt_tokens` can be None for backends that do not.
    """

    ttft_s: Optional[float]          # wall time to the first streamed token
    total_s: float                   # wall time for the whole request
    prompt_tokens: Optional[int]
    completion_tokens: int
    # Generation speed over the decode window only (excludes prompt + TTFT),
    # i.e. completion_tokens / (total - ttft). This is the number people mean
    # by "tokens/sec" and is the fair cross-backend comparison.
    gen_tokens_per_s: Optional[float]
    # Backend-reported speed when available (e.g. Ollama's own ns counters),
    # for cross-checking our wall-clock number. None if the backend is silent.
    backend_tokens_per_s: Optional[float] = None
    # Model id the backend reports actually served the request. If it differs
    # from what we asked for, the backend substituted a different (e.g. the only
    # loaded) model — the result is NOT for the requested model. None if the
    # backend doesn't echo it.
    served_model: Optional[str] = None
    output_text: Optional[str] = None  # populated only when --save-output

    def to_dict(self) -> Dict:
        return asdict(self)


def compute_gen_tps(
    completion_tokens: int, total_s: float, ttft_s: Optional[float]
) -> Optional[float]:
    """tokens/sec over the decode window; None if it can't be computed."""
    if completion_tokens <= 0:
        return None
    decode_window = total_s - ttft_s if ttft_s is not None else total_s
    if decode_window <= 0:
        return None
    return completion_tokens / decode_window


class Backend:
    """Interface every backend implements."""

    name = "base"

    def __init__(self, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
    ) -> RunMetrics:
        raise NotImplementedError

    def health(self) -> bool:
        """Return True if the server looks reachable. Best-effort."""
        raise NotImplementedError

    def available_models(self) -> Optional[List[str]]:
        """Model ids the server currently exposes, or None if it can't be read.

        Used to gate a benchmark: many backends (notably LM Studio) silently
        serve whatever model is loaded when asked for an unknown id, returning a
        normal 200. Checking availability first prevents recording a result that
        was actually produced by a different model.
        """
        raise NotImplementedError
