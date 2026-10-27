"""LM Studio backend — streaming OpenAI-compatible /v1/chat/completions.

LM Studio emits Server-Sent Events: lines prefixed with `data: `, terminated by
`data: [DONE]`. We time the first content delta for TTFT. Token usage arrives in
a final `usage` object when the server is asked to include it; if the server
omits it we fall back to counting streamed deltas so completion_tokens is never
zero for a real response.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

import requests

from .base import Backend, RunMetrics, compute_gen_tps


class LMStudioBackend(Backend):
    name = "lmstudio"

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/v1/models", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def available_models(self) -> Optional[List[str]]:
        try:
            r = requests.get(f"{self.base_url}/v1/models", timeout=5)
            if r.status_code != 200:
                return None
            data = r.json()
        except (requests.RequestException, ValueError):
            return None
        # OpenAI-compatible shape: {"data": [{"id": "..."}, ...]}
        return [m.get("id") for m in data.get("data", []) if m.get("id")]

    def generate(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
    ) -> RunMetrics:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "stream": True,
            # ask for a usage block on the final SSE chunk
            "stream_options": {"include_usage": True},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        start = time.perf_counter()
        ttft: Optional[float] = None
        chunks = []
        delta_count = 0
        usage = {}
        served_model: Optional[str] = None

        headers = {"Content-Type": "application/json"}
        with requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers=headers,
            data=json.dumps(payload),
            timeout=self.timeout,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                # Every chunk carries the id of the model that actually answered.
                if served_model is None and obj.get("model"):
                    served_model = obj["model"]
                if obj.get("usage"):
                    usage = obj["usage"]
                for choice in obj.get("choices", []):
                    piece = (choice.get("delta") or {}).get("content", "")
                    if piece:
                        if ttft is None:
                            ttft = time.perf_counter() - start
                        chunks.append(piece)
                        delta_count += 1

        total = time.perf_counter() - start

        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        if completion_tokens == 0:
            # server didn't report usage; approximate by streamed delta count
            completion_tokens = delta_count
        prompt_tokens = usage.get("prompt_tokens")
        prompt_tokens = int(prompt_tokens) if prompt_tokens is not None else None

        return RunMetrics(
            ttft_s=ttft,
            total_s=total,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            gen_tokens_per_s=compute_gen_tps(completion_tokens, total, ttft),
            backend_tokens_per_s=None,  # LM Studio has no separate server timing
            served_model=served_model,
            output_text="".join(chunks),
        )
