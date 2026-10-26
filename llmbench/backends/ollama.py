"""Ollama backend — streaming /api/generate.

Ollama streams newline-delimited JSON objects. We time the first one for TTFT
and read the final object's nanosecond counters (`eval_count`, `eval_duration`)
for the backend's own tokens/sec, which we keep alongside our wall-clock number.
"""

from __future__ import annotations

import json
import time
from typing import Optional

import requests

from .base import Backend, RunMetrics, compute_gen_tps


class OllamaBackend(Backend):
    name = "ollama"

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def generate(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
    ) -> RunMetrics:
        options = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens  # Ollama's token cap

        payload = {
            "model": model,
            "prompt": prompt,
            "options": options,
            "stream": True,
        }

        start = time.perf_counter()
        ttft: Optional[float] = None
        chunks = []
        final = {}

        with requests.post(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload),
            timeout=self.timeout,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                piece = obj.get("response", "")
                if piece and ttft is None:
                    ttft = time.perf_counter() - start
                if piece:
                    chunks.append(piece)
                if obj.get("done"):
                    final = obj
                    break

        total = time.perf_counter() - start

        completion_tokens = int(final.get("eval_count", 0) or 0)
        prompt_tokens = final.get("prompt_eval_count")
        prompt_tokens = int(prompt_tokens) if prompt_tokens is not None else None

        # Ollama's own decode speed, from its nanosecond counters.
        backend_tps = None
        eval_ns = final.get("eval_duration")
        if completion_tokens and eval_ns:
            backend_tps = completion_tokens / (eval_ns / 1e9)

        return RunMetrics(
            ttft_s=ttft,
            total_s=total,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            gen_tokens_per_s=compute_gen_tps(completion_tokens, total, ttft),
            backend_tokens_per_s=backend_tps,
            output_text="".join(chunks),
        )
